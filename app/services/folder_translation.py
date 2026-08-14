"""Translate image folders page-by-page and persist completed previews."""

import os
from pathlib import Path

from app.core.models import BlockStatus, Page, Project, TextBlock, TranslationContext, utc_now
from app.persistence.project_repository import ProjectRepository
from app.services.export import ExportService
from app.services.translation.codex_cli import CodexCliTranslationProvider
from app.services.workflow import (
    CancellationToken,
    ProgressCallback,
    ProgressUpdate,
    WorkflowCancelled,
    WorkflowIssue,
    WorkflowResult,
)


class FolderTranslationService:
    def __init__(
        self,
        provider: CodexCliTranslationProvider,
        font_path: str | os.PathLike[str],
    ) -> None:
        self._provider = provider
        self._font_path = Path(font_path)

    async def run(
        self,
        project: Project,
        project_dir: str | os.PathLike[str],
        cancellation: CancellationToken | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> WorkflowResult:
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        directory = Path(project_dir).resolve()
        result = project.model_copy(deep=True)
        context = TranslationContext(
            default_source_language=result.settings.default_source_language,
            target_language=result.settings.target_language,
            characters=result.characters,
            glossary=result.glossary,
            previous_summary=result.previous_summary,
            translation_note=result.translation_note,
        )
        issues: list[WorkflowIssue] = []

        for current, page in enumerate(result.pages, 1):
            token.raise_if_cancelled()
            try:
                candidate = await self._translated_page(page, directory, context, token)
                warnings, preview_issues = ExportService.render_page_preview(
                    candidate,
                    directory,
                    directory / "previews" / f"preview-page-{page.id}.png",
                    self._font_path,
                    clean_background=True,
                    cancellation=token,
                )
                token.raise_if_cancelled()
                page.blocks = candidate.blocks
                result.updated_at = utc_now()
                ProjectRepository.save(result, directory)
                issues.extend(
                    WorkflowIssue(
                        stage="translation",
                        message=item.message,
                        page_id=item.page_id,
                        block_id=item.block_id,
                        recoverable=True,
                    )
                    for item in warnings
                )
                issues.extend(
                    WorkflowIssue(
                        stage="translation",
                        message=item.message,
                        page_id=item.page_id,
                        block_id=item.block_id,
                        recoverable=item.recoverable,
                    )
                    for item in preview_issues
                )
                message = "Page translated and preview saved"
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
                message = "Page failed; continuing"

            if on_progress is not None:
                on_progress(
                    ProgressUpdate(
                        stage="translation",
                        current=current,
                        total=len(result.pages),
                        message=message,
                        page_id=page.id,
                    )
                )

        token.raise_if_cancelled()
        ProjectRepository.save(result, directory)
        return WorkflowResult(project=result, issues=tuple(issues))

    async def _translated_page(
        self,
        page: Page,
        project_dir: Path,
        context: TranslationContext,
        token: CancellationToken,
    ) -> Page:
        image_path = Path(page.source_path)
        if not image_path.is_absolute():
            image_path = project_dir / image_path
        image_path = image_path.resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"image not found: {image_path}")

        raw_blocks = await self._provider.translate_page_image(
            page.model_copy(deep=True), image_path, context
        )
        token.raise_if_cancelled()
        blocks = [TextBlock.model_validate(item).model_copy(deep=True) for item in raw_blocks]
        for block in blocks:
            block.translated_text = " ".join(block.translated_text.splitlines())
        if any(block.page_id != page.id for block in blocks):
            raise ValueError("provider returned a block for the wrong page")
        if any(block.status is not BlockStatus.TRANSLATED for block in blocks):
            raise ValueError("provider returned a block that is not translated")
        if len({block.id for block in blocks}) != len(blocks):
            raise ValueError("provider returned duplicate block IDs")
        self._preserve_matching_block_identities(page.blocks, blocks)
        if len({block.id for block in blocks}) != len(blocks):
            raise ValueError("matched blocks have duplicate IDs")
        return page.model_copy(update={"blocks": blocks}, deep=True)

    @staticmethod
    def _preserve_matching_block_identities(
        existing_blocks: list[TextBlock], new_blocks: list[TextBlock]
    ) -> None:
        candidates = sorted(
            (
                (-overlap, new_index, existing_index)
                for new_index, new_block in enumerate(new_blocks)
                for existing_index, existing_block in enumerate(existing_blocks)
                if (overlap := _bbox_iou(new_block, existing_block)) >= 0.5
            )
        )
        matched_new: set[int] = set()
        matched_existing: set[int] = set()
        for _, new_index, existing_index in candidates:
            if new_index in matched_new or existing_index in matched_existing:
                continue
            existing = existing_blocks[existing_index]
            new_blocks[new_index].id = existing.id
            new_blocks[new_index].created_at = existing.created_at
            matched_new.add(new_index)
            matched_existing.add(existing_index)


def _bbox_iou(first: TextBlock, second: TextBlock) -> float:
    left = max(first.bbox.x, second.bbox.x)
    top = max(first.bbox.y, second.bbox.y)
    right = min(first.bbox.x + first.bbox.width, second.bbox.x + second.bbox.width)
    bottom = min(first.bbox.y + first.bbox.height, second.bbox.y + second.bbox.height)
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first.bbox.width * first.bbox.height + second.bbox.width * second.bbox.height
    return intersection / (union - intersection)
