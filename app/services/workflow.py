"""Asynchronous detection, OCR, and translation orchestration."""

from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from threading import Event
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.coordinates import clamp_bbox
from app.core.language import resolve_source_language
from app.core.models import (
    BlockStatus,
    Page,
    Project,
    SourceLanguage,
    TextBlock,
    TranslationContext,
    TranslationInput,
    TranslationResult,
    utc_now,
)
from app.services.ocr.base import OcrProvider
from app.services.text_detection.base import TextDetectionProvider
from app.services.translation.base import TranslationProvider
from app.services.types import ImageInput

type WorkflowStage = Literal["detection", "ocr", "translation"]
type WorkflowMode = Literal["end_to_end", "ocr", "translate"]
type ProgressCallback = Callable[["ProgressUpdate"], None]


class WorkflowCancelled(RuntimeError):
    """Raised when a caller cancels a workflow run."""


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise WorkflowCancelled("workflow cancelled")


class _WorkflowValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProgressUpdate(_WorkflowValue):
    stage: WorkflowStage
    current: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    message: str
    page_id: UUID | None = None
    block_id: UUID | None = None

    @model_validator(mode="after")
    def validate_progress(self) -> "ProgressUpdate":
        if self.current > self.total:
            raise ValueError("current cannot exceed total")
        return self


class WorkflowIssue(_WorkflowValue):
    stage: WorkflowStage
    message: str
    page_id: UUID | None = None
    block_id: UUID | None = None
    recoverable: bool


class WorkflowResult(_WorkflowValue):
    project: Project
    issues: tuple[WorkflowIssue, ...] = ()


class WorkflowService:
    def __init__(
        self,
        detector: TextDetectionProvider,
        ocr_provider: OcrProvider,
        translation_provider: TranslationProvider,
    ) -> None:
        self._detector = detector
        self._ocr_provider = ocr_provider
        self._translation_provider = translation_provider

    async def run(
        self,
        project: Project,
        project_dir: str | Path | None = None,
        page_ids: Iterable[UUID] | None = None,
        cancellation: CancellationToken | None = None,
        on_progress: ProgressCallback | None = None,
        block_ids: Iterable[UUID] | None = None,
        mode: WorkflowMode = "end_to_end",
    ) -> WorkflowResult:
        if mode not in ("end_to_end", "ocr", "translate"):
            raise ValueError(f"invalid workflow mode: {mode!r}")

        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        result = project.model_copy(deep=True)
        selected_page_ids = None if page_ids is None else set(page_ids)
        selected_block_ids = None if block_ids is None else set(block_ids)
        pages = [
            page
            for page in result.pages
            if selected_page_ids is None or page.id in selected_page_ids
        ]
        issues: list[WorkflowIssue] = []
        issue_stage: WorkflowStage = "translation" if mode == "translate" else "detection"
        if selected_page_ids is not None:
            known_page_ids = {page.id for page in result.pages}
            for page_id in sorted(selected_page_ids - known_page_ids, key=str):
                issues.append(
                    WorkflowIssue(
                        stage=issue_stage,
                        message=f"requested page not found: {page_id}",
                        page_id=page_id,
                        recoverable=True,
                    )
                )

        if selected_block_ids is not None:
            scoped_block_ids = {block.id for page in pages for block in page.blocks}
            for block_id in sorted(selected_block_ids - scoped_block_ids, key=str):
                issues.append(
                    WorkflowIssue(
                        stage="translation" if mode == "translate" else "ocr",
                        message=f"requested block not found in selected pages: {block_id}",
                        block_id=block_id,
                        recoverable=True,
                    )
                )

        if mode == "translate":
            translation_items: list[tuple[TextBlock, SourceLanguage]] = []
            translatable_statuses = {
                BlockStatus.OCR_REVIEWED,
                BlockStatus.TRANSLATED,
                BlockStatus.TRANSLATION_REVIEWED,
            }
            for page in pages:
                for block in page.blocks:
                    if selected_block_ids is not None and block.id not in selected_block_ids:
                        continue
                    if block.status not in translatable_statuses:
                        issues.append(
                            WorkflowIssue(
                                stage="translation",
                                message=(
                                    "OCR review is required before translation "
                                    f"(status={block.status.value})"
                                ),
                                page_id=page.id,
                                block_id=block.id,
                                recoverable=True,
                            )
                        )
                        continue
                    language = resolve_source_language(result.settings, page, block)
                    if language is SourceLanguage.AUTO:
                        block.status = BlockStatus.LANGUAGE_REVIEW_REQUIRED
                        block.updated_at = utc_now()
                        issues.append(
                            WorkflowIssue(
                                stage="translation",
                                message="source language must be confirmed before translation",
                                page_id=page.id,
                                block_id=block.id,
                                recoverable=True,
                            )
                        )
                        continue
                    translation_items.append((block, language))

            await self._translate(result, translation_items, issues, token, on_progress)
            token.raise_if_cancelled()
            return WorkflowResult(project=result, issues=tuple(issues))

        processing_pages = (
            pages
            if selected_block_ids is None
            else [
                page
                for page in pages
                if any(block.id in selected_block_ids for block in page.blocks)
            ]
        )
        usable_pages: list[tuple[Page, ImageInput]] = []

        for current, page in enumerate(processing_pages, 1):
            token.raise_if_cancelled()
            image = self._image_input(page, project_dir)
            if not image.path.is_file():
                issues.append(
                    WorkflowIssue(
                        stage="detection",
                        message=f"image not found: {image.path}",
                        page_id=page.id,
                        recoverable=True,
                    )
                )
                self._progress(
                    on_progress,
                    "detection",
                    current,
                    len(processing_pages),
                    "Image missing; page skipped",
                    page_id=page.id,
                )
                continue

            usable_pages.append((page, image))
            if page.blocks or selected_block_ids is not None:
                message = "Detection skipped; page already has blocks"
            else:
                await self._detect(page, image, issues, token)
                message = "Detection complete"
            self._progress(
                on_progress,
                "detection",
                current,
                len(processing_pages),
                message,
                page_id=page.id,
            )

        ocr_items = [
            (page, image, block)
            for page, image in usable_pages
            for block in page.blocks
            if selected_block_ids is None or block.id in selected_block_ids
        ]
        translation_items: list[tuple[TextBlock, SourceLanguage]] = []
        for current, (page, image, block) in enumerate(ocr_items, 1):
            token.raise_if_cancelled()
            language = resolve_source_language(result.settings, page, block)
            if language is SourceLanguage.AUTO:
                block.status = BlockStatus.LANGUAGE_REVIEW_REQUIRED
                block.updated_at = utc_now()
                issues.append(
                    WorkflowIssue(
                        stage="ocr",
                        message="source language must be confirmed before OCR",
                        page_id=page.id,
                        block_id=block.id,
                        recoverable=True,
                    )
                )
                message = "OCR requires language review"
            else:
                recognized = await self._recognize(
                    page,
                    image,
                    block,
                    language,
                    issues,
                    token,
                )
                if recognized:
                    translation_items.append((block, language))
                    message = "OCR complete"
                else:
                    message = "OCR failed"
            self._progress(
                on_progress,
                "ocr",
                current,
                len(ocr_items),
                message,
                page_id=page.id,
                block_id=block.id,
            )

        if mode == "end_to_end":
            await self._translate(result, translation_items, issues, token, on_progress)
        token.raise_if_cancelled()
        return WorkflowResult(project=result, issues=tuple(issues))

    async def _detect(
        self,
        page: Page,
        image: ImageInput,
        issues: list[WorkflowIssue],
        token: CancellationToken,
    ) -> None:
        token.raise_if_cancelled()
        try:
            regions = await self._detector.detect(image)
            token.raise_if_cancelled()
        except WorkflowCancelled:
            raise
        except Exception as error:
            token.raise_if_cancelled()
            issues.append(
                WorkflowIssue(
                    stage="detection",
                    message=str(error),
                    page_id=page.id,
                    recoverable=bool(getattr(error, "recoverable", False)),
                )
            )
            return

        for region in regions:
            token.raise_if_cancelled()
            try:
                bbox = clamp_bbox(region.bbox, page.width, page.height)
            except (AttributeError, TypeError, ValueError) as error:
                issues.append(
                    WorkflowIssue(
                        stage="detection",
                        message=f"invalid detected region: {error}",
                        page_id=page.id,
                        recoverable=True,
                    )
                )
                continue
            page.blocks.append(
                TextBlock(page_id=page.id, bbox=bbox, reading_order=len(page.blocks) + 1)
            )

    async def _recognize(
        self,
        page: Page,
        image: ImageInput,
        block: TextBlock,
        language: SourceLanguage,
        issues: list[WorkflowIssue],
        token: CancellationToken,
    ) -> bool:
        token.raise_if_cancelled()
        try:
            recognized = await self._ocr_provider.recognize(image, block.bbox, language)
            token.raise_if_cancelled()
        except WorkflowCancelled:
            raise
        except Exception as error:
            token.raise_if_cancelled()
            block.status = BlockStatus.ERROR
            block.updated_at = utc_now()
            issues.append(
                WorkflowIssue(
                    stage="ocr",
                    message=str(error),
                    page_id=page.id,
                    block_id=block.id,
                    recoverable=bool(getattr(error, "recoverable", False)),
                )
            )
            return False

        block.source_text = recognized.source_text
        block.ocr_confidence = recognized.confidence
        block.ocr_provider = recognized.provider
        block.status = BlockStatus.OCR_COMPLETE
        block.updated_at = utc_now()
        return True

    async def _translate(
        self,
        project: Project,
        items: list[tuple[TextBlock, SourceLanguage]],
        issues: list[WorkflowIssue],
        token: CancellationToken,
        on_progress: ProgressCallback | None,
    ) -> None:
        if not items:
            return

        context = TranslationContext(
            default_source_language=project.settings.default_source_language,
            target_language=project.settings.target_language,
            characters=project.characters,
            glossary=project.glossary,
            previous_summary=project.previous_summary,
            translation_note=project.translation_note,
        )
        items_by_page: dict[UUID, list[tuple[TextBlock, SourceLanguage]]] = {}
        for block, language in items:
            items_by_page.setdefault(block.page_id, []).append((block, language))

        current = 0
        for page in project.pages:
            page_items = items_by_page.get(page.id, [])
            if not page_items:
                continue
            token.raise_if_cancelled()
            inputs = [
                TranslationInput(
                    id=block.id,
                    source_language=language,
                    source_text=block.source_text,
                    reading_order=block.reading_order,
                )
                for block, language in page_items
            ]
            try:
                raw_results = await self._translation_provider.translate_blocks(inputs, context)
                token.raise_if_cancelled()
                results = [TranslationResult.model_validate(item) for item in raw_results]
                result_ids = [item.id for item in results]
                expected_ids = {item.id for item in inputs}
                duplicate_ids = {
                    item_id for item_id, count in Counter(result_ids).items() if count > 1
                }
                missing_ids = expected_ids - set(result_ids)
                unknown_ids = set(result_ids) - expected_ids
                if duplicate_ids or missing_ids or unknown_ids or len(results) != len(inputs):
                    raise ValueError(
                        "invalid translation IDs "
                        f"(missing={sorted(map(str, missing_ids))}, "
                        f"duplicate={sorted(map(str, duplicate_ids))}, "
                        f"unknown={sorted(map(str, unknown_ids))})"
                    )
            except WorkflowCancelled:
                raise
            except Exception as error:
                token.raise_if_cancelled()
                issues.append(
                    WorkflowIssue(
                        stage="translation",
                        message=str(error),
                        page_id=page.id,
                        recoverable=bool(getattr(error, "recoverable", True)),
                    )
                )
                for block, _ in page_items:
                    token.raise_if_cancelled()
                    current += 1
                    self._progress(
                        on_progress,
                        "translation",
                        current,
                        len(items),
                        "Translation failed; page unchanged",
                        page_id=page.id,
                        block_id=block.id,
                    )
                continue

            by_id = {item.id: item for item in results}
            for block, _ in page_items:
                token.raise_if_cancelled()
                translated = by_id[block.id]
                block.translated_text = translated.translated_text
                block.note = translated.note
                block.status = BlockStatus.TRANSLATED
                block.updated_at = utc_now()
                current += 1
                self._progress(
                    on_progress,
                    "translation",
                    current,
                    len(items),
                    "Translation complete",
                    page_id=page.id,
                    block_id=block.id,
                )

    @staticmethod
    def _image_input(page: Page, project_dir: str | Path | None) -> ImageInput:
        path = Path(page.source_path)
        if not path.is_absolute() and project_dir is not None:
            path = Path(project_dir) / path
        return ImageInput(path=path.resolve(), width=page.width, height=page.height)

    @staticmethod
    def _progress(
        callback: ProgressCallback | None,
        stage: WorkflowStage,
        current: int,
        total: int,
        message: str,
        *,
        page_id: UUID | None = None,
        block_id: UUID | None = None,
    ) -> None:
        if callback is not None:
            callback(
                ProgressUpdate(
                    stage=stage,
                    current=current,
                    total=total,
                    message=message,
                    page_id=page_id,
                    block_id=block_id,
                )
            )
