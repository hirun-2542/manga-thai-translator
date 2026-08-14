import asyncio
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from app.core.models import BlockStatus, BoundingBox, Page, Project, TextBlock
from app.persistence.project_repository import ProjectRepository
from app.services.export import ExportService
from app.services.folder_translation import FolderTranslationService
from app.services.workflow import CancellationToken, ProgressUpdate, WorkflowCancelled


def _font_path() -> Path:
    return Path(ImageFont.truetype("NotoSans-Regular.ttf", 12).path)


def _page(path: Path) -> Page:
    Image.new("RGB", (160, 120), "white").save(path)
    return Page(source_path=str(path), width=160, height=120)


def _translated_block(page: Page, text: str) -> TextBlock:
    return TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=20, y=20, width=120, height=80),
        reading_order=1,
        source_text="source",
        translated_text=text,
        status=BlockStatus.TRANSLATED,
    )


class FakeImageProvider:
    def __init__(self, failing_names: set[str] | None = None) -> None:
        self.failing_names = failing_names or set()
        self.calls: list[tuple[Page, Path, object]] = []

    async def translate_page_image(self, page, image_path, context):
        path = Path(image_path)
        self.calls.append((page, path, context))
        if path.name in self.failing_names:
            raise RuntimeError(f"failed {path.name}")
        return [_translated_block(page, f"แปล {path.stem}")]


def test_folder_translation_normalizes_provider_linebreaks_before_preview_and_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = _page(tmp_path / "linebreaks.png")
    project = Project(name="folder linebreaks", pages=[page])
    provider = FakeImageProvider()
    preview_texts: list[str] = []

    async def translate_page_image(page, image_path, context):
        provider.calls.append((page, Path(image_path), context))
        return [_translated_block(page, "บรรทัดหนึ่ง\nบรรทัดสอง\r\nบรรทัดสาม")]

    provider.translate_page_image = translate_page_image

    def capture_preview(page, *args, **kwargs):
        preview_texts.append(page.blocks[0].translated_text)
        return [], []

    monkeypatch.setattr(ExportService, "render_page_preview", capture_preview)

    result = asyncio.run(FolderTranslationService(provider, _font_path()).run(project, tmp_path))

    expected = "บรรทัดหนึ่ง บรรทัดสอง บรรทัดสาม"
    assert preview_texts == [expected]
    assert result.project.pages[0].blocks[0].translated_text == expected
    assert ProjectRepository.load(tmp_path).pages[0].blocks[0].translated_text == expected


def test_folder_translation_retranslates_existing_blocks_and_preserves_matching_identity(
    tmp_path: Path,
) -> None:
    first = _page(tmp_path / "หน้า1.png")
    second = _page(tmp_path / "หน้า2.png")
    existing = _translated_block(second, "คำแปลเดิม")
    existing.source_text = "mock source"
    existing.status = BlockStatus.TRANSLATION_REVIEWED
    second.blocks = [existing]
    project = Project(name="folder", pages=[first, second])
    ProjectRepository.save(project, tmp_path)
    source_bytes = {
        path: path.read_bytes() for path in (tmp_path / "หน้า1.png", tmp_path / "หน้า2.png")
    }
    provider = FakeImageProvider()
    progress: list[ProgressUpdate] = []

    result = asyncio.run(
        FolderTranslationService(provider, _font_path()).run(
            project,
            tmp_path,
            on_progress=progress.append,
        )
    )

    assert project.pages[0].blocks == []
    assert [call[1].name for call in provider.calls] == ["หน้า1.png", "หน้า2.png"]
    assert result.project.pages[0].blocks[0].translated_text == "แปล หน้า1"
    assert result.project.pages[1].blocks[0].id == existing.id
    assert result.project.pages[1].blocks[0].created_at == existing.created_at
    assert result.project.pages[1].blocks[0].source_text == "source"
    assert result.project.pages[1].blocks[0].translated_text == "แปล หน้า2"
    assert result.project.pages[1].blocks[0].status is BlockStatus.TRANSLATED
    assert result.issues == ()
    assert [update.stage for update in progress] == ["translation", "translation"]
    assert [update.current for update in progress] == [1, 2]
    assert all(update.total == 2 for update in progress)
    assert ProjectRepository.load(tmp_path) == result.project
    assert {path.name for path in (tmp_path / "previews").glob("*.png")} == {
        f"preview-page-{first.id}.png",
        f"preview-page-{second.id}.png",
    }
    assert all(path.read_bytes() == content for path, content in source_bytes.items())


def test_folder_translation_replaces_non_overlapping_mock_block(tmp_path: Path) -> None:
    page = _page(tmp_path / "page.png")
    mock = _translated_block(page, "คำแปลจำลอง")
    mock.bbox = BoundingBox(x=0, y=0, width=10, height=10)
    page.blocks = [mock]
    provider = FakeImageProvider()

    result = asyncio.run(
        FolderTranslationService(provider, _font_path()).run(
            Project(name="replace mock", pages=[page]), tmp_path
        )
    )

    replacement = result.project.pages[0].blocks[0]
    assert [call[1].name for call in provider.calls] == ["page.png"]
    assert replacement.id != mock.id
    assert replacement.translated_text == "แปล page"
    assert replacement.translated_text != "คำแปลจำลอง"


def test_folder_translation_page_failure_continues_and_saves_success(tmp_path: Path) -> None:
    failed = _page(tmp_path / "bad.png")
    succeeded = _page(tmp_path / "good.png")
    project = Project(name="continue", pages=[failed, succeeded])
    ProjectRepository.save(project, tmp_path)
    provider = FakeImageProvider({"bad.png"})

    result = asyncio.run(FolderTranslationService(provider, _font_path()).run(project, tmp_path))

    assert [call[1].name for call in provider.calls] == ["bad.png", "good.png"]
    assert result.project.pages[0].blocks == []
    assert result.project.pages[1].blocks[0].translated_text == "แปล good"
    assert len(result.issues) == 1
    assert result.issues[0].page_id == failed.id
    assert "failed bad.png" in result.issues[0].message
    assert not (tmp_path / "previews" / f"preview-page-{failed.id}.png").exists()
    assert (tmp_path / "previews" / f"preview-page-{succeeded.id}.png").is_file()
    assert ProjectRepository.load(tmp_path) == result.project


def test_folder_translation_cancellation_keeps_completed_page_saved(tmp_path: Path) -> None:
    first = _page(tmp_path / "first.png")
    second = _page(tmp_path / "second.png")
    project = Project(name="cancel", pages=[first, second])
    ProjectRepository.save(project, tmp_path)
    provider = FakeImageProvider()
    token = CancellationToken()

    def cancel_after_first(update: ProgressUpdate) -> None:
        if update.current == 1:
            token.cancel()

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        asyncio.run(
            FolderTranslationService(provider, _font_path()).run(
                project,
                tmp_path,
                cancellation=token,
                on_progress=cancel_after_first,
            )
        )

    saved = ProjectRepository.load(tmp_path)
    assert [call[1].name for call in provider.calls] == ["first.png"]
    assert saved.pages[0].blocks[0].translated_text == "แปล first"
    assert saved.pages[1].blocks == []
    assert (tmp_path / "previews" / f"preview-page-{first.id}.png").is_file()
    assert not (tmp_path / "previews" / f"preview-page-{second.id}.png").exists()
