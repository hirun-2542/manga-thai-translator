import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.models import (
    BlockStatus,
    BoundingBox,
    Page,
    Project,
    ProjectSettings,
    SourceLanguage,
    TextBlock,
    TranslationResult,
)
from app.services.ocr import MockOcrProvider, OcrResult
from app.services.text_detection import DetectedRegion, MockTextDetectionProvider
from app.services.translation import MockTranslationProvider
from app.services.workflow import (
    CancellationToken,
    ProgressUpdate,
    WorkflowCancelled,
    WorkflowService,
)


def _page(path: Path | str, *, language: SourceLanguage | None = None) -> Page:
    return Page(
        source_path=str(path),
        width=100,
        height=200,
        source_language=language,
    )


def _block(
    page: Page,
    order: int,
    *,
    language: SourceLanguage | None = None,
    translated_text: str = "",
) -> TextBlock:
    return TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=order * 10, y=10, width=10, height=20),
        reading_order=order,
        source_language=language,
        translated_text=translated_text,
    )


def _run(
    project: Project,
    tmp_path: Path,
    *,
    detector=None,
    ocr=None,
    translation=None,
    **kwargs,
):
    service = WorkflowService(
        detector or MockTextDetectionProvider(),
        ocr or MockOcrProvider(),
        translation or MockTranslationProvider(),
    )
    return asyncio.run(service.run(project, project_dir=tmp_path, **kwargs))


def test_empty_page_runs_default_mock_workflow_end_to_end(tmp_path: Path) -> None:
    image = tmp_path / "page.png"
    image.touch()
    project = Project(
        name="test",
        settings=ProjectSettings(default_source_language=SourceLanguage.JA),
        pages=[_page(image.name)],
    )

    result = _run(project, tmp_path)

    assert project.pages[0].blocks == []
    assert len(result.project.pages[0].blocks) == 1
    block = result.project.pages[0].blocks[0]
    assert block.reading_order == 1
    assert block.source_text == "おかえり"
    assert block.translated_text == "แปลไทย: おかえり"
    assert block.ocr_provider == "mock-ocr"
    assert block.status is BlockStatus.TRANSLATED
    assert result.issues == ()


def test_mixed_languages_preserve_block_ids_and_order(tmp_path: Path) -> None:
    image = tmp_path / "mixed.png"
    image.touch()
    page = _page(image)
    page.blocks = [
        _block(page, 8, language=SourceLanguage.KO),
        _block(page, 2, language=SourceLanguage.EN),
        _block(page, 5, language=SourceLanguage.ZH_HANT),
    ]
    original_ids = [block.id for block in page.blocks]

    class ReversedTranslation:
        async def translate_blocks(self, blocks, context):
            return [
                TranslationResult(
                    id=block.id, translated_text=f"translated-{block.source_language}"
                )
                for block in reversed(blocks)
            ]

    result = _run(
        Project(name="mixed", pages=[page]),
        tmp_path,
        translation=ReversedTranslation(),
    )
    blocks = result.project.pages[0].blocks

    assert [block.id for block in blocks] == original_ids
    assert [block.reading_order for block in blocks] == [8, 2, 5]
    assert [block.translated_text for block in blocks] == [
        "translated-ko",
        "translated-en",
        "translated-zh-Hant",
    ]


def test_manual_blocks_skip_detection_and_are_preserved(tmp_path: Path) -> None:
    image = tmp_path / "manual.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    manual = _block(page, 7)
    page.blocks = [manual]

    class DetectorMustNotRun:
        async def detect(self, image):
            raise AssertionError("detector called")

    result = _run(
        Project(name="manual", pages=[page]),
        tmp_path,
        detector=DetectorMustNotRun(),
    )

    assert len(result.project.pages[0].blocks) == 1
    assert result.project.pages[0].blocks[0].id == manual.id
    assert result.project.pages[0].blocks[0].reading_order == 7


def test_auto_requires_review_without_calling_ocr_or_translation(tmp_path: Path) -> None:
    image = tmp_path / "auto.png"
    image.touch()
    page = _page(image)
    page.blocks = [_block(page, 0)]

    class ProviderMustNotRun:
        async def recognize(self, image, bbox, source_language):
            raise AssertionError("OCR called")

        async def translate_blocks(self, blocks, context):
            raise AssertionError("translation called")

    provider = ProviderMustNotRun()
    result = _run(
        Project(name="auto", pages=[page]),
        tmp_path,
        ocr=provider,
        translation=provider,
    )

    block = result.project.pages[0].blocks[0]
    assert block.status is BlockStatus.LANGUAGE_REVIEW_REQUIRED
    assert [(issue.stage, issue.block_id, issue.recoverable) for issue in result.issues] == [
        ("ocr", block.id, True)
    ]


def test_relative_image_path_uses_project_dir_and_page_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    image = source / "หน้า.png"
    image.touch()
    seen = []

    class CapturingDetector:
        async def detect(self, image):
            seen.append(image)
            return []

    project = Project(name="relative", pages=[_page("source/หน้า.png")])
    _run(project, tmp_path, detector=CapturingDetector())

    assert seen[0].path == image.resolve()
    assert (seen[0].width, seen[0].height) == (100, 200)


def test_missing_image_and_invalid_region_are_issues_and_batch_continues(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.png"
    existing.touch()
    missing = _page("missing.png", language=SourceLanguage.JA)
    valid = _page(existing.name, language=SourceLanguage.JA)
    detector = MockTextDetectionProvider(
        [
            DetectedRegion(
                bbox=BoundingBox(x=200, y=0, width=10, height=10),
                confidence=0.5,
            ),
            DetectedRegion(
                bbox=BoundingBox(x=90, y=190, width=20, height=20),
                confidence=0.9,
            ),
            DetectedRegion(
                bbox=BoundingBox(x=10, y=20, width=20, height=20),
                confidence=0.9,
            ),
        ]
    )

    result = _run(Project(name="errors", pages=[missing, valid]), tmp_path, detector=detector)

    assert result.project.pages[0].blocks == []
    assert len(result.project.pages[1].blocks) == 2
    assert result.project.pages[1].blocks[0].bbox == BoundingBox(x=90, y=190, width=10, height=10)
    assert [block.reading_order for block in result.project.pages[1].blocks] == [1, 2]
    assert [issue.stage for issue in result.issues] == ["detection", "detection"]


def test_page_and_block_provider_failures_continue(tmp_path: Path) -> None:
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_image.touch()
    second_image.touch()
    failed_page = _page(first_image, language=SourceLanguage.EN)
    good_page = _page(second_image, language=SourceLanguage.EN)
    good_page.blocks = [_block(good_page, 0), _block(good_page, 1)]

    class FailingDetector:
        async def detect(self, image):
            raise RuntimeError("detection failed")

    class OneFailureOcr:
        name = "one-failure"

        async def recognize(self, image, bbox, source_language):
            if bbox.x == 0:
                raise RuntimeError("OCR failed")
            return OcrResult(source_text="ok", confidence=0.8, provider=self.name)

    result = _run(
        Project(name="continue", pages=[failed_page, good_page]),
        tmp_path,
        detector=FailingDetector(),
        ocr=OneFailureOcr(),
    )

    assert result.project.pages[1].blocks[0].status is BlockStatus.ERROR
    assert result.project.pages[1].blocks[1].status is BlockStatus.TRANSLATED
    assert [(issue.stage, issue.page_id, issue.block_id) for issue in result.issues] == [
        ("detection", failed_page.id, None),
        ("ocr", good_page.id, good_page.blocks[0].id),
    ]


@pytest.mark.parametrize("response_kind", ["missing", "duplicate", "unknown"])
def test_invalid_translation_mapping_never_applies_partial_results(
    tmp_path: Path,
    response_kind: str,
) -> None:
    image = tmp_path / "translation.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    page.blocks = [
        _block(page, 0, translated_text="old-0"),
        _block(page, 1, translated_text="old-1"),
    ]

    class InvalidTranslation:
        async def translate_blocks(self, blocks, context):
            first = TranslationResult(id=blocks[0].id, translated_text="new-0")
            if response_kind == "missing":
                return [first]
            if response_kind == "duplicate":
                return [first, first]
            return [
                first,
                TranslationResult(id=uuid4(), translated_text="unknown"),
            ]

    result = _run(
        Project(name="invalid translation", pages=[page]),
        tmp_path,
        translation=InvalidTranslation(),
    )

    assert [block.translated_text for block in result.project.pages[0].blocks] == [
        "old-0",
        "old-1",
    ]
    assert [block.status for block in result.project.pages[0].blocks] == [
        BlockStatus.OCR_COMPLETE,
        BlockStatus.OCR_COMPLETE,
    ]
    assert len(result.issues) == 1
    assert result.issues[0].stage == "translation"
    assert "invalid translation IDs" in result.issues[0].message


def test_translation_provider_failure_is_reported_without_crashing(tmp_path: Path) -> None:
    image = tmp_path / "provider-failure.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    page.blocks = [_block(page, 0, translated_text="keep")]

    class FailingTranslation:
        async def translate_blocks(self, blocks, context):
            raise RuntimeError("provider unavailable")

    result = _run(
        Project(name="provider failure", pages=[page]),
        tmp_path,
        translation=FailingTranslation(),
    )

    block = result.project.pages[0].blocks[0]
    assert block.translated_text == "keep"
    assert block.status is BlockStatus.OCR_COMPLETE
    assert [(issue.stage, issue.message, issue.page_id) for issue in result.issues] == [
        ("translation", "provider unavailable", page.id)
    ]


def test_translate_all_pages_batches_by_page_and_continues_after_failure(tmp_path: Path) -> None:
    first = _page("first.png", language=SourceLanguage.EN)
    second = _page("second.png", language=SourceLanguage.JA)
    third = _page("third.png", language=SourceLanguage.KO)
    first.blocks = [_block(first, 3, translated_text="keep-1"), _block(first, 1)]
    second.blocks = [_block(second, 7, translated_text="keep-2")]
    third.blocks = [_block(third, 2)]
    for page in (first, second, third):
        for block in page.blocks:
            block.source_text = f"source-{block.reading_order}"
            block.status = BlockStatus.OCR_REVIEWED
    calls = []
    updates = []

    class PerPageTranslation:
        async def translate_blocks(self, blocks, context):
            calls.append([(block.id, block.reading_order) for block in blocks])
            if blocks[0].id == second.blocks[0].id:
                raise TimeoutError("page timed out")
            return [
                TranslationResult(id=block.id, translated_text=f"new-{block.reading_order}")
                for block in reversed(blocks)
            ]

    result = _run(
        Project(name="per page", pages=[first, second, third]),
        tmp_path,
        translation=PerPageTranslation(),
        mode="translate",
        on_progress=updates.append,
    )

    assert calls == [
        [(first.blocks[0].id, 3), (first.blocks[1].id, 1)],
        [(second.blocks[0].id, 7)],
        [(third.blocks[0].id, 2)],
    ]
    assert [block.translated_text for block in result.project.pages[0].blocks] == ["new-3", "new-1"]
    assert result.project.pages[1].blocks[0].translated_text == "keep-2"
    assert result.project.pages[1].blocks[0].status is BlockStatus.OCR_REVIEWED
    assert result.project.pages[2].blocks[0].translated_text == "new-2"
    assert [(issue.page_id, issue.message, issue.recoverable) for issue in result.issues] == [
        (second.id, "page timed out", True)
    ]
    assert [
        (update.current, update.total, update.page_id, update.block_id) for update in updates
    ] == [
        (1, 4, first.id, first.blocks[0].id),
        (2, 4, first.id, first.blocks[1].id),
        (3, 4, second.id, second.blocks[0].id),
        (4, 4, third.id, third.blocks[0].id),
    ]


def test_translate_all_pages_cancellation_stops_before_next_page_request(tmp_path: Path) -> None:
    first = _page("first.png", language=SourceLanguage.EN)
    second = _page("second.png", language=SourceLanguage.EN)
    first.blocks = [_block(first, 1)]
    second.blocks = [_block(second, 1)]
    for page in (first, second):
        page.blocks[0].source_text = "reviewed"
        page.blocks[0].status = BlockStatus.OCR_REVIEWED
    token = CancellationToken()
    calls = []
    project = Project(name="cancel pages", pages=[first, second])
    before = project.model_dump()

    class CapturingTranslation:
        async def translate_blocks(self, blocks, context):
            calls.append([block.id for block in blocks])
            return [TranslationResult(id=blocks[0].id, translated_text="new")]

    def cancel_after_first_page(update: ProgressUpdate) -> None:
        if update.page_id == first.id:
            token.cancel()

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        _run(
            project,
            tmp_path,
            translation=CapturingTranslation(),
            mode="translate",
            cancellation=token,
            on_progress=cancel_after_first_page,
        )

    assert calls == [[first.blocks[0].id]]
    assert project.model_dump() == before


def test_cancellation_after_provider_await_leaves_original_unchanged(tmp_path: Path) -> None:
    image = tmp_path / "cancel.png"
    image.touch()
    page = _page(image, language=SourceLanguage.JA)
    project = Project(name="cancel", pages=[page])
    before = project.model_dump()
    token = CancellationToken()

    class CancellingDetector:
        async def detect(self, image):
            token.cancel()
            return [
                DetectedRegion(
                    bbox=BoundingBox(x=0, y=0, width=10, height=10),
                    confidence=1,
                )
            ]

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        _run(project, tmp_path, detector=CancellingDetector(), cancellation=token)

    assert token.is_cancelled is True
    assert project.model_dump() == before


def test_progress_updates_are_deterministic_and_immutable(tmp_path: Path) -> None:
    image = tmp_path / "progress.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    page.blocks = [_block(page, 0), _block(page, 1)]
    updates = []

    _run(
        Project(name="progress", pages=[page]),
        tmp_path,
        on_progress=updates.append,
    )

    assert [
        (update.stage, update.current, update.total, update.page_id, update.block_id)
        for update in updates
    ] == [
        ("detection", 1, 1, page.id, None),
        ("ocr", 1, 2, page.id, page.blocks[0].id),
        ("ocr", 2, 2, page.id, page.blocks[1].id),
        ("translation", 1, 2, page.id, page.blocks[0].id),
        ("translation", 2, 2, page.id, page.blocks[1].id),
    ]
    with pytest.raises(ValidationError):
        updates[0].current = 2
    with pytest.raises(ValidationError, match="cannot exceed total"):
        ProgressUpdate(stage="ocr", current=2, total=1, message="invalid")


def test_ocr_mode_runs_only_selected_block_without_translation(tmp_path: Path) -> None:
    image = tmp_path / "selected-block.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    page.blocks = [_block(page, 3), _block(page, 1)]
    original_ids = [block.id for block in page.blocks]
    ocr_calls = []
    updates = []

    class CapturingOcr(MockOcrProvider):
        async def recognize(self, image, bbox, source_language):
            ocr_calls.append(bbox)
            return await super().recognize(image, bbox, source_language)

    class TranslationMustNotRun:
        async def translate_blocks(self, blocks, context):
            raise AssertionError("translation called")

    result = _run(
        Project(name="selected block", pages=[page]),
        tmp_path,
        ocr=CapturingOcr(),
        translation=TranslationMustNotRun(),
        block_ids=[page.blocks[1].id, page.blocks[1].id],
        mode="ocr",
        on_progress=updates.append,
    )
    blocks = result.project.pages[0].blocks

    assert len(ocr_calls) == 1
    assert [block.id for block in blocks] == original_ids
    assert [block.reading_order for block in blocks] == [3, 1]
    assert [block.status for block in blocks] == [
        BlockStatus.DETECTED,
        BlockStatus.OCR_COMPLETE,
    ]
    assert [(update.stage, update.current, update.total) for update in updates] == [
        ("detection", 1, 1),
        ("ocr", 1, 1),
    ]


def test_ocr_mode_detects_only_selected_blank_page(tmp_path: Path) -> None:
    first_image = tmp_path / "first-selected.png"
    second_image = tmp_path / "second-unselected.png"
    first_image.touch()
    second_image.touch()
    first = _page(first_image, language=SourceLanguage.KO)
    second = _page(second_image, language=SourceLanguage.KO)

    class TranslationMustNotRun:
        async def translate_blocks(self, blocks, context):
            raise AssertionError("translation called")

    result = _run(
        Project(name="selected page", pages=[first, second]),
        tmp_path,
        translation=TranslationMustNotRun(),
        page_ids=[first.id, first.id],
        mode="ocr",
    )

    assert len(result.project.pages[0].blocks) == 1
    assert result.project.pages[0].blocks[0].status is BlockStatus.OCR_COMPLETE
    assert result.project.pages[1].blocks == []


def test_translate_mode_requires_review_and_allows_retranslation(tmp_path: Path) -> None:
    page = _page("missing-is-fine.png", language=SourceLanguage.JA)
    statuses = [
        BlockStatus.OCR_REVIEWED,
        BlockStatus.TRANSLATED,
        BlockStatus.TRANSLATION_REVIEWED,
        BlockStatus.OCR_COMPLETE,
        BlockStatus.OCR_REVIEWED,
    ]
    page.blocks = [_block(page, index) for index in range(len(statuses))]
    for index, (block, status) in enumerate(zip(page.blocks, statuses, strict=True)):
        block.source_text = f"text-{index}"
        block.status = status
    page.blocks[-1].source_language = SourceLanguage.AUTO
    seen_ids = []
    updates = []

    class ProviderMustNotRun:
        async def detect(self, image):
            raise AssertionError("detection called")

        async def recognize(self, image, bbox, source_language):
            raise AssertionError("OCR called")

    class CapturingTranslation(MockTranslationProvider):
        async def translate_blocks(self, blocks, context):
            seen_ids.extend(block.id for block in blocks)
            return await super().translate_blocks(blocks, context)

    result = _run(
        Project(name="review gate", pages=[page]),
        tmp_path,
        detector=ProviderMustNotRun(),
        ocr=ProviderMustNotRun(),
        translation=CapturingTranslation(),
        mode="translate",
        on_progress=updates.append,
    )
    blocks = result.project.pages[0].blocks

    assert seen_ids == [block.id for block in page.blocks[:3]]
    assert [block.status for block in blocks[:3]] == [BlockStatus.TRANSLATED] * 3
    assert blocks[3].status is BlockStatus.OCR_COMPLETE
    assert blocks[4].status is BlockStatus.LANGUAGE_REVIEW_REQUIRED
    assert page.blocks[4].status is BlockStatus.OCR_REVIEWED
    assert blocks[4].updated_at > page.blocks[4].updated_at
    assert [(issue.block_id, issue.recoverable) for issue in result.issues] == [
        (page.blocks[3].id, True),
        (page.blocks[4].id, True),
    ]
    assert "OCR review is required" in result.issues[0].message
    assert "source language must be confirmed" in result.issues[1].message
    assert [(update.current, update.total) for update in updates] == [(1, 3), (2, 3), (3, 3)]


def test_empty_selections_do_no_work(tmp_path: Path) -> None:
    image = tmp_path / "nothing.png"
    image.touch()
    page = _page(image, language=SourceLanguage.EN)
    page.blocks = [_block(page, 0)]

    class ProviderMustNotRun:
        async def detect(self, image):
            raise AssertionError("detection called")

        async def recognize(self, image, bbox, source_language):
            raise AssertionError("OCR called")

        async def translate_blocks(self, blocks, context):
            raise AssertionError("translation called")

    provider = ProviderMustNotRun()
    project = Project(name="empty selections", pages=[page])

    page_result = _run(
        project,
        tmp_path,
        detector=provider,
        ocr=provider,
        translation=provider,
        page_ids=[],
    )
    block_result = _run(
        project,
        tmp_path,
        detector=provider,
        ocr=provider,
        translation=provider,
        block_ids=[],
        mode="ocr",
    )

    assert page_result.project == project
    assert block_result.project == project
    assert page_result.issues == block_result.issues == ()


def test_unknown_and_out_of_page_ids_are_recoverable_issues(tmp_path: Path) -> None:
    first_image = tmp_path / "scope-first.png"
    second_image = tmp_path / "scope-second.png"
    first_image.touch()
    second_image.touch()
    first = _page(first_image, language=SourceLanguage.EN)
    second = _page(second_image, language=SourceLanguage.EN)
    first.blocks = [_block(first, 0)]
    second.blocks = [_block(second, 0)]
    unknown_page = uuid4()
    unknown_block = uuid4()

    result = _run(
        Project(name="unknown scope", pages=[first, second]),
        tmp_path,
        page_ids=[first.id, unknown_page],
        block_ids=[second.blocks[0].id, unknown_block],
        mode="ocr",
    )

    assert (result.issues[0].stage, result.issues[0].page_id, result.issues[0].block_id) == (
        "detection",
        unknown_page,
        None,
    )
    assert {issue.block_id for issue in result.issues[1:]} == {
        second.blocks[0].id,
        unknown_block,
    }
    assert all(issue.stage == "ocr" for issue in result.issues[1:])
    assert all(issue.recoverable for issue in result.issues)


def test_invalid_mode_fails_before_calls_or_copy_mutation(tmp_path: Path) -> None:
    page = _page("unused.png", language=SourceLanguage.EN)
    project = Project(name="invalid mode", pages=[page])
    before = project.model_dump()

    class ProviderMustNotRun:
        async def detect(self, image):
            raise AssertionError("detection called")

        async def recognize(self, image, bbox, source_language):
            raise AssertionError("OCR called")

        async def translate_blocks(self, blocks, context):
            raise AssertionError("translation called")

    provider = ProviderMustNotRun()
    with pytest.raises(ValueError, match="invalid workflow mode"):
        _run(
            project,
            tmp_path,
            detector=provider,
            ocr=provider,
            translation=provider,
            mode="invalid",
        )

    assert project.model_dump() == before


def test_translate_cancellation_leaves_input_project_unchanged(tmp_path: Path) -> None:
    page = _page("unused.png", language=SourceLanguage.EN)
    page.blocks = [_block(page, 0)]
    page.blocks[0].source_text = "reviewed"
    page.blocks[0].status = BlockStatus.OCR_REVIEWED
    project = Project(name="cancel translation", pages=[page])
    before = project.model_dump()
    token = CancellationToken()

    class CancellingTranslation:
        async def translate_blocks(self, blocks, context):
            token.cancel()
            return [TranslationResult(id=blocks[0].id, translated_text="new")]

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        _run(
            project,
            tmp_path,
            translation=CancellingTranslation(),
            cancellation=token,
            mode="translate",
        )

    assert project.model_dump() == before
