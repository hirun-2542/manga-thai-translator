import csv
import json
import os
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.core.models import (
    BlockStatus,
    BoundingBox,
    Page,
    Project,
    ProjectSettings,
    SourceLanguage,
    TextBlock,
    WritingMode,
)
from app.services.export import ExportProgress, ExportService
from app.services.workflow import CancellationToken, WorkflowCancelled


def _font_path() -> Path:
    return Path(ImageFont.truetype("NotoSans-Regular.ttf", 12).path)


def make_page(
    path: Path,
    identifier: int,
    blocks: list[dict] | None = None,
    *,
    language: SourceLanguage | None = None,
) -> Page:
    page = Page(
        id=UUID(int=identifier),
        source_path=str(path),
        width=160,
        height=120,
        source_language=language,
    )
    page.blocks = [
        TextBlock(
            id=UUID(int=identifier * 100 + index),
            page_id=page.id,
            bbox=item.pop("bbox", BoundingBox(x=10, y=10, width=120, height=80)),
            reading_order=item.pop("reading_order", index),
            **item,
        )
        for index, item in enumerate(blocks or [], 1)
    ]
    return page


def save_image(path: Path, color: str = "blue") -> bytes:
    Image.new("RGB", (160, 120), color).save(path)
    return path.read_bytes()


def test_structured_exports_are_complete_ordered_and_utf8(tmp_path: Path) -> None:
    first_path = tmp_path / "หน้า一.png"
    second_path = tmp_path / "หน้า二.png"
    save_image(first_path)
    save_image(second_path)
    first = make_page(
        first_path,
        1,
        [
            {
                "reading_order": 10,
                "source_language": SourceLanguage.JA,
                "source_text": "後",
                "translated_text": "หลัง",
            },
            {
                "reading_order": 2,
                "source_text": 'ก่อน, "quoted"\nบรรทัดใหม่',
                "translated_text": "คำแปลไทย",
                "ocr_provider": "mock-ocr",
                "speaker": "นางเอก",
                "note": "หมายเหตุ",
                "status": BlockStatus.TRANSLATION_REVIEWED,
                "writing_mode": WritingMode.VERTICAL,
            },
        ],
        language=SourceLanguage.KO,
    )
    second = make_page(
        second_path,
        2,
        [{"source_text": "第二頁", "translated_text": "หน้าสอง"}],
    )
    project = Project(
        name="ส่งออก",
        settings=ProjectSettings(default_source_language=SourceLanguage.ZH_HANT),
        pages=[first, second],
    )
    original = project.model_dump_json()

    result = ExportService.export(
        project,
        tmp_path,
        tmp_path / "exports",
        _font_path(),
    )

    assert json.loads(result.json_path.read_text(encoding="utf-8")) == project.model_dump(
        mode="json"
    )
    with result.csv_path.open(encoding="utf-8", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert list(rows[0]) == [
        "page_index",
        "page_id",
        "page_path",
        "block_id",
        "reading_order",
        "source_language",
        "ocr_provider",
        "speaker",
        "source",
        "translation",
        "note",
        "status",
        "writing_mode",
        "bbox_x",
        "bbox_y",
        "bbox_width",
        "bbox_height",
    ]
    assert [row["reading_order"] for row in rows] == ["2", "10", "1"]
    assert rows[0]["source_language"] == "ko"
    assert rows[0]["source"] == 'ก่อน, "quoted"\nบรรทัดใหม่'
    assert rows[0]["translation"] == "คำแปลไทย"
    assert rows[0]["ocr_provider"] == "mock-ocr"
    assert rows[0]["speaker"] == "นางเอก"
    assert rows[0]["status"] == "translation_reviewed"
    assert rows[0]["writing_mode"] == "vertical"
    assert '"' in result.csv_path.read_text(encoding="utf-8")
    text = result.txt_path.read_text(encoding="utf-8")
    assert text.index("[2] Source:") < text.index("[10] Source:") < text.index("Page 2:")
    assert "คำแปลไทย" in text and "第二頁" in text
    assert project.model_dump_json() == original


def test_preview_preserves_dimensions_source_and_marks_only_overflow(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.png"
    original = save_image(source, "green")
    page = make_page(
        source,
        3,
        [
            {
                "bbox": BoundingBox(x=10, y=10, width=135, height=80),
                "translated_text": "ข้อความสั้น",
            },
            {
                "bbox": BoundingBox(x=4, y=100, width=20, height=10),
                "translated_text": "ข้อความภาษาไทยที่ยาวมากและล้นกรอบแน่นอน",
            },
            {
                "bbox": BoundingBox(x=100, y=100, width=20, height=10),
                "translated_text": "",
            },
        ],
    )

    result = ExportService.export(
        Project(name="preview", pages=[page]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        background_color="#fffffe",
    )

    assert source.read_bytes() == original
    assert result.issues == ()
    assert [warning.block_id for warning in result.overflow_warnings] == [page.blocks[1].id]
    with Image.open(result.preview_paths[0]) as preview:
        assert preview.size == (160, 120)
        assert preview.getpixel((4, 100)) == (255, 0, 0)
        assert preview.getpixel((100, 100)) == (0, 128, 0)


def test_thai_graphemes_and_explicit_newlines_are_not_split() -> None:
    assert ExportService._graphemes("กำลัง") == ["กำ", "ลั", "ง"]
    assert ExportService._graphemes("e\u0301😀") == ["e\u0301", "😀"]
    font = ImageFont.truetype(_font_path(), 20)
    draw = ImageDraw.Draw(Image.new("RGB", (200, 100)))
    width = int(draw.textlength("กำ", font=font)) + 1

    lines = ExportService._wrap_text(draw, "กำกำ\nไทย", font, width)

    assert lines[:2] == ["กำ", "กำ"]
    assert "".join(lines[:2]) == "กำกำ"
    assert lines[2:] and "".join(lines[2:]) == "ไทย"


def test_bbox_is_clamped_and_bad_bbox_is_a_recoverable_issue(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    save_image(source)
    page = make_page(
        source,
        4,
        [
            {
                "bbox": BoundingBox(x=140, y=100, width=40, height=40),
                "translated_text": "พอดี",
            },
            {
                "bbox": BoundingBox(x=200, y=200, width=10, height=10),
                "translated_text": "นอกภาพ",
            },
        ],
    )

    result = ExportService.export(
        Project(name="bbox", pages=[page]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
    )

    assert result.preview_paths
    assert [(issue.page_id, issue.block_id) for issue in result.issues] == [
        (page.id, page.blocks[1].id)
    ]


def test_invalid_page_continues_and_progress_is_monotonic(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image", encoding="utf-8")
    valid = tmp_path / "valid.png"
    save_image(valid)
    first = make_page(invalid, 5)
    second = make_page(valid, 6)
    progress: list[ExportProgress] = []

    result = ExportService.export(
        Project(name="continue", pages=[first, second]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        on_progress=progress.append,
    )

    assert result.json_path.is_file()
    assert [item.current for item in progress] == [1, 2]
    assert all(item.total == 2 for item in progress)
    assert result.issues[0].page_id == first.id
    assert len(result.preview_paths) == 1
    assert str(second.id) in result.preview_paths[0].name


@pytest.mark.parametrize(
    ("font", "color", "error"),
    [
        ("missing.ttf", "white", FileNotFoundError),
        ("font.txt", "white", ValueError),
        ("valid", "not-a-color", ValueError),
    ],
)
def test_invalid_font_or_color_is_rejected_before_exports(
    tmp_path: Path,
    font: str,
    color: str,
    error: type[Exception],
) -> None:
    source = tmp_path / "page.png"
    save_image(source)
    output = tmp_path / "out"
    if font == "valid":
        font_path = _font_path()
    else:
        font_path = tmp_path / font
        if font_path.suffix == ".txt":
            font_path.write_text("not a font", encoding="utf-8")

    with pytest.raises(error):
        ExportService.export(
            Project(name="invalid", pages=[make_page(source, 7)]),
            tmp_path,
            output,
            font_path,
            background_color=color,
        )

    assert not output.exists()


def test_destination_collision_is_rejected_before_any_write(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    page_id = UUID(int=8)
    source = output / f"preview-page-0001-{page_id}.png"
    original = save_image(source)
    page = Page(id=page_id, source_path=str(source), width=160, height=120)

    with pytest.raises(ValueError, match="overwrite a source"):
        ExportService.export(
            Project(name="collision", pages=[page]),
            tmp_path,
            output,
            _font_path(),
        )

    assert source.read_bytes() == original
    assert sorted(output.iterdir()) == [source]


def test_atomic_failure_cleans_temporary_file(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "page.png"
    save_image(source)
    output = tmp_path / "out"

    def fail_replace(source_path, destination_path):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        ExportService.export(
            Project(name="atomic", pages=[make_page(source, 9)]),
            tmp_path,
            output,
            _font_path(),
        )

    assert list(output.iterdir()) == []


def test_cancellation_between_pages_keeps_completed_files_valid(tmp_path: Path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_bytes = save_image(first_path)
    second_bytes = save_image(second_path)
    project = Project(
        name="cancel",
        pages=[make_page(first_path, 10), make_page(second_path, 11)],
    )
    output = tmp_path / "out"
    token = CancellationToken()

    def cancel_after_first(progress: ExportProgress) -> None:
        if progress.current == 1:
            token.cancel()

    with pytest.raises(WorkflowCancelled, match="cancelled"):
        ExportService.export(
            project,
            tmp_path,
            output,
            _font_path(),
            cancellation=token,
            on_progress=cancel_after_first,
        )

    assert json.loads((output / "project-export.json").read_text(encoding="utf-8"))["name"] == (
        "cancel"
    )
    assert len(list(output.glob("preview-*.png"))) == 1
    assert not list(output.glob(".*.tmp"))
    assert first_path.read_bytes() == first_bytes
    assert second_path.read_bytes() == second_bytes
