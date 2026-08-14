import builtins
import csv
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

from app.core.models import (
    BlockStatus,
    BoundingBox,
    Page,
    Project,
    ProjectSettings,
    SourceLanguage,
    TextAlignment,
    TextBlock,
    WritingMode,
)
from app.services.export import ExportProgress, ExportService, RenderMetric
from app.services.system_fonts import FontResolutionError, SystemFont
from app.services.workflow import CancellationToken, WorkflowCancelled


def _font_path() -> Path:
    return Path(ImageFont.truetype("NotoSans-Regular.ttf", 12).path)


def _thai_font_path() -> Path:
    return Path(ImageFont.truetype("NotoSansThai-Regular.ttf", 12).path)


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


def save_complex_image(path: Path) -> Image.Image:
    image = Image.new("RGB", (160, 120), (40, 80, 120))
    draw = ImageDraw.Draw(image)
    for x in range(0, 160, 4):
        draw.line((x, 0, x, 119), fill=(160, 100, 60))
    draw.text(
        (34, 25),
        "EN",
        font=ImageFont.truetype(_font_path(), 18),
        fill="white",
        stroke_width=5,
        stroke_fill="black",
    )
    image.save(path)
    return image


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
                "typesetting_alignment": TextAlignment.RIGHT,
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

    assert result.preview_paths == (
        tmp_path / "exports" / "1.png",
        tmp_path / "exports" / "2.png",
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
        "text_alignment",
        "bbox_x",
        "bbox_y",
        "bbox_width",
        "bbox_height",
        "rotation_degrees",
        "mirror_horizontal",
        "mirror_vertical",
    ]
    assert [row["reading_order"] for row in rows] == ["2", "10", "1"]
    assert rows[0]["source_language"] == "ko"
    assert rows[0]["source"] == 'ก่อน, "quoted"\nบรรทัดใหม่'
    assert rows[0]["translation"] == "คำแปลไทย"
    assert rows[0]["ocr_provider"] == "mock-ocr"
    assert rows[0]["speaker"] == "นางเอก"
    assert rows[0]["status"] == "translation_reviewed"
    assert rows[0]["writing_mode"] == "vertical"
    assert rows[0]["text_alignment"] == "right"
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


def test_export_text_watermark_is_drawn_at_bottom_right(tmp_path: Path) -> None:
    source = tmp_path / "watermark-source.png"
    original = Image.new("RGB", (600, 400), "white")
    original.save(source)
    page = Page(source_path=str(source), width=600, height=400)

    result = ExportService.export(
        Project(name="watermark", pages=[page]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        watermark_text="แปลหลังเลิกงาน",
    )

    with Image.open(result.preview_paths[0]) as exported:
        difference = ImageChops.difference(original, exported.convert("RGB"))
        bounds = difference.getbbox()
    assert bounds is not None
    assert bounds[0] > original.width // 2
    assert bounds[1] > original.height // 2


def test_export_transparent_logo_scales_down_without_covering_transparent_pixels(
    tmp_path: Path,
) -> None:
    source = tmp_path / "logo-source.png"
    Image.new("RGB", (1000, 500), "white").save(source)
    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((200, 0, 399, 99), fill=(255, 0, 0, 255))
    logo.save(logo_path)
    page = Page(source_path=str(source), width=1000, height=500)

    result = ExportService.export(
        Project(name="logo", pages=[page]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        watermark_logo_path=logo_path,
    )

    with Image.open(result.preview_paths[0]) as exported:
        image = exported.convert("RGB")
        assert image.getpixel((800, 460)) == (255, 255, 255)
        assert image.getpixel((940, 460))[0] > 240
        assert image.getpixel((940, 460))[1:] == (0, 0)


@pytest.mark.parametrize("position", ["start", "end"])
def test_export_banner_is_numbered_as_first_or_last_page(
    tmp_path: Path,
    position: str,
) -> None:
    first_source = tmp_path / "first.png"
    second_source = tmp_path / "second.png"
    save_image(first_source, "blue")
    save_image(second_source, "green")
    banner = tmp_path / "banner.webp"
    Image.new("RGB", (90, 40), "orange").save(banner)
    progress: list[ExportProgress] = []

    result = ExportService.export(
        Project(
            name="banner",
            pages=[make_page(first_source, 31), make_page(second_source, 32)],
        ),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        banner_path=banner,
        banner_position=position,
        on_progress=progress.append,
    )

    assert [path.name for path in result.preview_paths] == ["1.png", "2.png", "3.png"]
    banner_index = 0 if position == "start" else 2
    with Image.open(result.preview_paths[banner_index]) as exported_banner:
        assert exported_banner.size == (90, 40)
        assert exported_banner.convert("RGB").getpixel((0, 0)) == (255, 165, 0)
    assert [item.current for item in progress] == [1, 2, 3]
    assert all(item.total == 3 for item in progress)


def test_export_branding_validation_happens_before_writes(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    save_image(source)
    page = make_page(source, 33)
    logo = tmp_path / "logo.png"
    save_image(logo)
    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image", encoding="utf-8")

    with pytest.raises(ValueError, match="text or logo"):
        ExportService.export(
            Project(name="both", pages=[page]),
            tmp_path,
            tmp_path / "both-out",
            _font_path(),
            watermark_text="mark",
            watermark_logo_path=logo,
        )
    with pytest.raises(ValueError, match="invalid banner position"):
        ExportService.export(
            Project(name="position", pages=[page]),
            tmp_path,
            tmp_path / "position-out",
            _font_path(),
            banner_path=logo,
            banner_position="middle",
        )
    with pytest.raises(ValueError, match="invalid watermark logo"):
        ExportService.export(
            Project(name="invalid", pages=[page]),
            tmp_path,
            tmp_path / "invalid-out",
            _font_path(),
            watermark_logo_path=invalid,
        )

    assert not (tmp_path / "both-out").exists()
    assert not (tmp_path / "position-out").exists()
    assert not (tmp_path / "invalid-out").exists()


def test_banner_source_collision_is_rejected_before_writes(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    save_image(source)
    output = tmp_path / "out"
    output.mkdir()
    banner = output / "1.png"
    original = save_image(banner, "orange")

    with pytest.raises(ValueError, match="overwrite a source"):
        ExportService.export(
            Project(name="collision", pages=[make_page(source, 34)]),
            tmp_path,
            output,
            _font_path(),
            banner_path=banner,
            banner_position="start",
        )

    assert banner.read_bytes() == original
    assert sorted(output.iterdir()) == [banner]


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


def test_thai_wrapping_prefers_qt_word_boundaries() -> None:
    font = ImageFont.truetype(_font_path(), 20)
    draw = ImageDraw.Draw(Image.new("RGB", (200, 100)))
    width = int(draw.textlength("ภาษา", font=font)) + 1

    lines = ExportService._wrap_text(draw, "ภาษาไทยดีมาก", font, width)

    assert lines == ["ภาษา", "ไทย", "ดี", "มาก"]


def test_mixed_thai_and_latin_wrapping_preserves_latin_whitespace() -> None:
    font = ImageFont.truetype(_font_path(), 20)
    draw = ImageDraw.Draw(Image.new("RGB", (200, 100)))
    width = int(draw.textlength("ภาษา", font=font)) + 1

    lines = ExportService._wrap_text(draw, "ภาษาไทย hello", font, width)

    assert lines == ["ภาษา", "ไทย", "hello"]


def test_overwide_thai_word_falls_back_to_grapheme_clusters() -> None:
    font = ImageFont.truetype(_font_path(), 20)
    draw = ImageDraw.Draw(Image.new("RGB", (200, 100)))

    lines = ExportService._wrap_text(draw, "กำลัง", font, 1)

    assert lines == ExportService._graphemes("กำลัง")


def test_single_page_preview_centers_and_fits_text_beyond_48px(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    save_image(source)
    page = make_page(
        source,
        12,
        [{"bbox": BoundingBox(x=10, y=10, width=140, height=100), "translated_text": "M"}],
    )
    destination = tmp_path / "preview.png"

    warnings, issues = ExportService.render_page_preview(page, tmp_path, destination, _font_path())

    assert warnings == issues == ()
    with Image.open(destination) as preview:
        crop = preview.crop((10, 10, 150, 110))
        ink = ImageChops.difference(crop, Image.new("RGB", crop.size, "white")).getbbox()
    assert ink is not None
    assert ink[3] - ink[1] > 48
    assert abs((ink[0] + ink[2]) / 2 - 70) <= 3
    assert abs((ink[1] + ink[3]) / 2 - 50) <= 3


def test_flat_textured_preview_uses_solid_ring_median_without_a_halo(tmp_path: Path) -> None:
    source = tmp_path / "pattern.png"
    ring_color = (210, 230, 240)
    original_image = Image.new("RGB", (160, 120), ring_color)
    draw = ImageDraw.Draw(original_image)
    for y in range(20, 90):
        shade = (y - 20) // 7
        draw.line((30, y, 119, y), fill=(210 + shade, 230 + shade, 240 + shade))
    glyph_color = (20, 130, 150)
    draw.text(
        (34, 25),
        "EN",
        font=ImageFont.truetype(_font_path(), 18),
        fill=glyph_color,
        stroke_width=5,
        stroke_fill="white",
    )
    original_image.save(source)
    original_bytes = source.read_bytes()
    bbox = BoundingBox(x=30, y=20, width=90, height=70)
    page = make_page(source, 13, [{"bbox": bbox, "translated_text": "ไ"}])
    destination = tmp_path / "nested" / "preview.png"

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == issues == ()
    assert source.read_bytes() == original_bytes
    assert not list(destination.parent.glob(".*.tmp"))
    with Image.open(source) as unchanged, Image.open(destination) as preview:
        unchanged.load()
        preview.load()
        assert preview.size == unchanged.size
        glyph_pixels = [
            (x, y)
            for y in range(20, 55)
            for x in range(30, 70)
            if unchanged.getpixel((x, y)) in {glyph_color, (255, 255, 255)}
        ]
        assert glyph_pixels
        assert all(preview.getpixel(point) != unchanged.getpixel(point) for point in glyph_pixels)
        untouched_texture = [(x, y) for y in range(22, 40) for x in range(110, 120)]
        assert all(preview.getpixel(point) == (210, 230, 240) for point in untouched_texture)
        thai_region = {preview.getpixel((x, y)) for y in range(25, 85) for x in range(65, 95)}
        assert (0, 0, 0) in thai_region
        assert (255, 255, 255) not in thai_region
        changed_inside = False
        for y in range(unchanged.height):
            for x in range(unchanged.width):
                changed = preview.getpixel((x, y)) != unchanged.getpixel((x, y))
                if 30 <= x < 120 and 20 <= y < 90:
                    changed_inside |= changed
                else:
                    assert not changed
        assert changed_inside


def test_clean_preview_uses_bbox_median_for_mask_without_an_exterior_ring(
    tmp_path: Path,
) -> None:
    source = tmp_path / "blue.png"
    original = Image.new("RGB", (160, 120), (62, 212, 254))
    draw = ImageDraw.Draw(original)
    glyph_color = (142, 236, 255)
    draw.text((10, 10), "EN", font=ImageFont.truetype(_font_path(), 18), fill=glyph_color)
    original.save(source)
    page = make_page(
        source,
        19,
        [{"bbox": BoundingBox(x=0, y=0, width=160, height=120), "translated_text": "ไ"}],
    )
    destination = tmp_path / "blue-preview.png"

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == issues == ()
    with Image.open(destination) as preview:
        glyph_pixels = [
            (x, y)
            for y in range(10, 30)
            for x in range(10, 40)
            if original.getpixel((x, y)) == glyph_color
        ]
        assert glyph_pixels
        assert all(preview.getpixel(point) != glyph_color for point in glyph_pixels)
        assert preview.getpixel((150, 10)) == (62, 212, 254)


def test_smooth_high_variance_gradient_is_cleanup_safe(tmp_path: Path) -> None:
    source = tmp_path / "gradient.png"
    original = Image.new("RGB", (160, 120))
    for y in range(original.height):
        for x in range(original.width):
            jitter = 2 if (x + y) % 2 else -2
            original.putpixel((x, y), (20 + jitter, 50 + x + jitter, 90 + x + jitter))
    smooth_field = original.copy()
    draw = ImageDraw.Draw(original)
    draw.line((14, 10, 14, 109), fill=(10, 220, 250), width=2)
    draw.line((146, 10, 146, 109), fill=(10, 220, 250), width=2)
    background = original.copy()
    font = ImageFont.truetype(_font_path(), 18)
    glyph_origin = (36, 76)
    shadow_origin = (38, 83)
    main_bounds = draw.textbbox(glyph_origin, "EN", font=font)
    shadow_bounds = draw.textbbox(shadow_origin, "EN", font=font)
    glyph_bounds = (
        min(main_bounds[0], shadow_bounds[0]),
        min(main_bounds[1], shadow_bounds[1]),
        max(main_bounds[2], shadow_bounds[2]),
        max(main_bounds[3], shadow_bounds[3]),
    )
    shadow_mask = Image.new("L", original.size)
    ImageDraw.Draw(shadow_mask).text(shadow_origin, "EN", font=font, fill=25)
    original.paste((0, 120, 180), mask=shadow_mask)
    glyph_mask = Image.new("L", original.size)
    ImageDraw.Draw(glyph_mask).text(glyph_origin, "EN", font=font, fill=255)
    original.paste((90, 220, 245), mask=glyph_mask)
    original.save(source)
    bbox = BoundingBox(x=30, y=20, width=100, height=80)
    page = make_page(
        source,
        22,
        [{"bbox": bbox, "translated_text": "ไ", "typesetting_stroke_width": None}],
    )
    destination = tmp_path / "gradient-preview.png"

    assert max(ImageStat.Stat(original).stddev) > 35
    detail = max(
        ImageStat.Stat(
            ImageChops.difference(
                smooth_field,
                smooth_field.filter(ImageFilter.GaussianBlur(2)),
            )
        ).mean
    )
    assert 1 < detail <= 2.1
    cleaned = ExportService._reconstruct_gradient(
        original.crop((26, 16, 134, 104)),
        original.crop((18, 16, 26, 104)),
        original.crop((134, 16, 142, 104)),
    )
    assert cleaned is not None
    glyph_pixels = [
        (x, y)
        for y in range(glyph_bounds[1], glyph_bounds[3])
        for x in range(glyph_bounds[0], glyph_bounds[2])
        if max(
            abs(original.getpixel((x, y))[channel] - background.getpixel((x, y))[channel])
            for channel in range(3)
        )
        >= 4
    ]
    assert glyph_pixels
    glyph_residuals = [
        max(
            abs(original.getpixel(point)[channel] - background.getpixel(point)[channel])
            for channel in range(3)
        )
        for point in glyph_pixels
    ]
    assert max(glyph_residuals) >= 50
    assert any(residual < 50 for residual in glyph_residuals)
    shadow_pixels = [point for point in glyph_pixels if point[1] >= 100]
    assert shadow_pixels
    assert all(
        max(
            abs(cleaned.getpixel((x - 26, y - 16))[channel] - background.getpixel((x, y))[channel])
            for channel in range(3)
        )
        <= 6
        for x, y in glyph_pixels
    )
    assert all(
        max(
            abs(cleaned.getpixel((x - 26, y - 16))[channel] - background.getpixel((x, y))[channel])
            for channel in range(3)
        )
        <= 6
        for y in range(16, 104)
        for x in range(26, 134)
    )
    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == issues == ()
    with Image.open(destination) as preview:
        assert any(
            max(
                abs(preview.getpixel((x, y))[channel] - cleaned.getpixel((x - 26, y - 16))[channel])
                for channel in range(3)
            )
            >= 50
            for y in range(20, 100)
            for x in range(30, 130)
        )
        assert all(
            preview.getpixel((x, y)) == original.getpixel((x, y))
            for y in range(original.height)
            for x in range(original.width)
            if not (26 <= x < 134 and 16 <= y < 104)
        )
        assert all(
            max(
                abs(preview.getpixel(point)[channel] - background.getpixel(point)[channel])
                for channel in range(3)
            )
            <= 6
            for point in shadow_pixels
        )


def test_smooth_gradient_without_masking_dependencies_is_preserved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "gradient.png"
    original = Image.new("RGB", (160, 120))
    draw = ImageDraw.Draw(original)
    for x in range(original.width):
        draw.line((x, 0, x, 119), fill=(20, 50 + x, 90 + x))
    draw.text((65, 45), "EN", font=ImageFont.truetype(_font_path(), 18), fill="white")
    original.save(source)
    bbox = BoundingBox(x=30, y=20, width=100, height=80)
    page = make_page(source, 23, [{"bbox": bbox, "translated_text": "ไ"}])
    destination = tmp_path / "preview.png"
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setitem(sys.modules, "numpy", None)

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == ()
    assert len(issues) == 1
    assert issues[0].block_id == page.blocks[0].id
    assert issues[0].recoverable
    with Image.open(destination) as preview:
        assert (
            preview.crop((30, 20, 130, 100)).tobytes()
            == original.crop((30, 20, 130, 100)).tobytes()
        )


def test_complex_preview_preserves_block_and_reports_cleanup_issue(tmp_path: Path) -> None:
    source = tmp_path / "complex.png"
    original = save_complex_image(source)
    destination = tmp_path / "complex-preview.png"
    page = make_page(
        source,
        20,
        [{"bbox": BoundingBox(x=0, y=0, width=160, height=120), "translated_text": "ไ"}],
    )

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == ()
    assert len(issues) == 1
    assert issues[0].recoverable
    assert issues[0].page_id == page.id
    assert issues[0].block_id == page.blocks[0].id
    assert "cleanup skipped" in issues[0].message
    with Image.open(destination) as preview:
        assert preview.tobytes() == original.tobytes()


def test_manual_cleanup_override_typesets_complex_block_only(tmp_path: Path) -> None:
    source = tmp_path / "complex.png"
    original = save_complex_image(source)
    original_bytes = source.read_bytes()
    bbox = BoundingBox(x=130, y=70, width=60, height=70)
    page = make_page(
        source,
        24,
        [{"bbox": bbox, "translated_text": "ไ", "typesetting_stroke_width": None}],
    )
    override_path = tmp_path / "cleanups" / f"page-{page.id}" / f"block-{page.blocks[0].id}.png"
    override_path.parent.mkdir(parents=True)
    Image.new("L", (46, 66), 25).save(override_path)
    result = ExportService.export(
        Project(name="manual-cleanup", pages=[page]),
        tmp_path,
        tmp_path / "out",
        _font_path(),
        clean_background=True,
    )

    assert result.overflow_warnings == result.issues == ()
    assert source.read_bytes() == original_bytes
    with Image.open(result.preview_paths[0]) as preview:
        rendered_levels = {
            sum(preview.getpixel((x, y))) for y in range(70, 120) for x in range(130, 160)
        }
        assert max(rendered_levels) - sum((25, 25, 25)) >= 300
        assert preview.getpixel((114, 54)) == (40, 80, 120)
        assert all(
            preview.getpixel((x, y)) == original.getpixel((x, y))
            for y in range(original.height)
            for x in range(original.width)
            if not (130 <= x < 160 and 70 <= y < 120)
        )


def test_preview_reuses_source_text_color_stroke_and_height(tmp_path: Path) -> None:
    source = tmp_path / "styled.png"
    background = (40, 170, 230)
    fill = (245, 250, 255)
    stroke = (15, 85, 120)
    original = Image.new("RGB", (160, 120), background)
    bbox = BoundingBox(x=30, y=25, width=100, height=65)
    ImageDraw.Draw(original).text(
        (47, 31),
        "EN",
        font=ImageFont.truetype(_font_path(), 44),
        fill=fill,
        stroke_width=3,
        stroke_fill=stroke,
    )
    original.save(source)
    page = make_page(
        source,
        25,
        [{"bbox": bbox, "source_text": "EN", "translated_text": "ไ"}],
    )
    block = page.blocks[0]
    override = tmp_path / "cleanups" / f"page-{page.id}" / f"block-{block.id}.png"
    override.parent.mkdir(parents=True)
    Image.new("RGB", (132, 97), background).save(override)
    destination = tmp_path / "styled-preview.png"

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == issues == ()
    style = ExportService._estimate_source_text_style(original, (30, 25, 130, 90), "EN")
    assert style is not None
    assert max(abs(style[0][index] - fill[index]) for index in range(3)) <= 12
    assert style[1] is not None
    assert max(abs(style[1][index] - stroke[index]) for index in range(3)) <= 20
    assert 35 <= style[3] <= 55
    with Image.open(destination) as preview:
        rendered = preview.crop((30, 25, 130, 90))
        colors = rendered.getcolors(maxcolors=rendered.width * rendered.height) or []
        assert any(
            max(abs(color[index] - fill[index]) for index in range(3)) <= 12
            for _count, color in colors
        )


def test_preview_emits_selected_runtime_render_metric(tmp_path: Path) -> None:
    source = tmp_path / "metric.png"
    save_image(source, "white")
    page = make_page(
        source,
        28,
        [
            {
                "bbox": BoundingBox(x=30, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_font_size": 24,
            }
        ],
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "metric-preview.png",
        _font_path(),
        on_metric=metrics.append,
    )

    assert warnings == issues == ()
    assert metrics == [
        RenderMetric(
            block_id=page.blocks[0].id,
            font_size_px=24,
            fill_rgb=(0, 0, 0),
            stroke_rgb=None,
            font_label=_font_path().name,
            stroke_width_px=0,
        )
    ]


def test_preview_applies_block_color_and_stroke_overrides(tmp_path: Path) -> None:
    source = tmp_path / "override.png"
    save_image(source, "white")
    page = make_page(
        source,
        281,
        [
            {
                "bbox": BoundingBox(x=20, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_fill_color": "#102030",
                "typesetting_stroke_color": "#A0B0C0",
                "typesetting_stroke_width": 0,
            }
        ],
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "override-preview.png",
        _font_path(),
        on_metric=metrics.append,
    )

    assert warnings == issues == ()
    assert metrics[0].fill_rgb == (16, 32, 48)
    assert metrics[0].stroke_rgb is None
    assert metrics[0].stroke_width_px == 0


def test_preview_treats_legacy_none_stroke_width_as_zero(tmp_path: Path) -> None:
    source = tmp_path / "stroke-auto.png"
    save_image(source, "white")
    page = make_page(
        source,
        282,
        [
            {
                "bbox": BoundingBox(x=20, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_stroke_color": "#A0B0C0",
                "typesetting_stroke_width": None,
            }
        ],
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "stroke-auto-preview.png",
        _font_path(),
        on_metric=metrics.append,
    )

    assert warnings == issues == ()
    assert metrics[0].stroke_rgb is None
    assert metrics[0].stroke_width_px == 0


def test_preview_keeps_explicit_positive_stroke_width(tmp_path: Path) -> None:
    source = tmp_path / "stroke-positive.png"
    save_image(source, "white")
    page = make_page(
        source,
        285,
        [
            {
                "bbox": BoundingBox(x=20, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_stroke_color": "#A0B0C0",
                "typesetting_stroke_width": 3,
            }
        ],
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "stroke-positive-preview.png",
        _font_path(),
        on_metric=metrics.append,
    )

    assert warnings == issues == ()
    assert metrics[0].stroke_rgb == (160, 176, 192)
    assert metrics[0].stroke_width_px == 3


def test_missing_block_font_falls_back_with_recoverable_issue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "missing-font.png"
    save_image(source, "white")
    page = make_page(
        source,
        283,
        [
            {
                "bbox": BoundingBox(x=20, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_font_family": "Missing Family",
                "typesetting_font_style": "Regular",
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.export.service.resolve_system_font",
        lambda *_args: (_ for _ in ()).throw(FontResolutionError("not installed")),
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "missing-font-preview.png",
        _font_path(),
        on_metric=metrics.append,
    )

    assert warnings == ()
    assert len(issues) == 1
    assert str(page.blocks[0].id) in issues[0].message
    assert issues[0].recoverable
    assert metrics[0].font_label == _font_path().name


def test_preview_applies_successful_block_font_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "override-font.png"
    save_image(source, "white")
    page = make_page(
        source,
        284,
        [
            {
                "bbox": BoundingBox(x=20, y=20, width=100, height=80),
                "translated_text": "ไ",
                "typesetting_font_family": "Test Thai",
                "typesetting_font_style": "Bold",
            }
        ],
    )
    fake_font = SystemFont(
        family="Test Thai",
        style="Bold",
        path=_font_path(),
        display_label="Test Thai / Bold",
    )
    monkeypatch.setattr(
        "app.services.export.service.resolve_system_font",
        lambda *_args: fake_font,
    )
    monkeypatch.setattr(
        "app.services.export.service.load_pillow_font",
        lambda _font, size: ImageFont.truetype(_font_path(), size),
    )
    metrics: list[RenderMetric] = []

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "override-font-preview.png",
        _font_path(),
        clean_background=False,
        on_metric=metrics.append,
    )

    assert warnings == issues == ()
    assert metrics[0].font_label == "Test Thai / Bold"


def test_preview_applies_exact_font_size_and_clockwise_rotation(tmp_path: Path) -> None:
    source = tmp_path / "typesetting.png"
    Image.new("RGB", (160, 120), "white").save(source)
    bbox = BoundingBox(x=30, y=20, width=100, height=80)

    def render(font_size: int, rotation: float, name: str) -> tuple[int, int]:
        page = make_page(
            source,
            26,
            [
                {
                    "bbox": bbox,
                    "translated_text": "ไ",
                    "typesetting_font_size": font_size,
                    "rotation_degrees": rotation,
                }
            ],
        )
        destination = tmp_path / name
        warnings, issues = ExportService.render_page_preview(
            page,
            tmp_path,
            destination,
            _font_path(),
            clean_background=False,
        )
        assert warnings == issues == ()
        with Image.open(destination) as preview:
            difference = ImageChops.difference(
                preview.crop((30, 20, 130, 100)).convert("RGB"),
                Image.new("RGB", (100, 80), "white"),
            )
            bounds = difference.getbbox()
        assert bounds is not None
        return bounds[2] - bounds[0], bounds[3] - bounds[1]

    small = render(24, 0.0, "small.png")
    large = render(48, 0.0, "large.png")
    rotated = render(48, 30.0, "rotated.png")

    assert large[1] > small[1]
    assert rotated != large


def test_rotated_region_background_preserves_pixels_outside_region_polygon(
    tmp_path: Path,
) -> None:
    source = tmp_path / "rotated-region.png"
    Image.new("RGB", (160, 120), "black").save(source)
    page = make_page(
        source,
        261,
        [
            {
                "bbox": BoundingBox(x=50, y=40, width=60, height=30),
                "translated_text": "ไ",
                "rotation_degrees": 45,
            }
        ],
    )
    destination = tmp_path / "rotated-region-preview.png"

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        background_color="white",
        clean_background=False,
    )

    assert warnings == issues == ()
    with Image.open(destination) as preview:
        assert preview.getpixel((48, 23)) == (0, 0, 0)
        assert preview.getpixel((69, 30)) == (255, 255, 255)


def test_text_mirror_axes_are_independent_and_composable() -> None:
    arguments = ("F", 100, 80, _font_path(), "white")
    options = {"font_size": 48, "fill_color": (0, 0, 0)}

    original, _ = ExportService._render_text_box(*arguments, **options)
    horizontal, _ = ExportService._render_text_box(
        *arguments,
        mirror_horizontal=True,
        **options,
    )
    vertical, _ = ExportService._render_text_box(
        *arguments,
        mirror_vertical=True,
        **options,
    )
    both, _ = ExportService._render_text_box(
        *arguments,
        mirror_horizontal=True,
        mirror_vertical=True,
        **options,
    )

    assert (
        ImageChops.difference(
            horizontal,
            original.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        ).getbbox()
        is None
    )
    assert (
        ImageChops.difference(
            vertical,
            original.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        ).getbbox()
        is None
    )
    assert (
        ImageChops.difference(
            both,
            original.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(
                Image.Transpose.FLIP_TOP_BOTTOM
            ),
        ).getbbox()
        is None
    )


def test_text_alignment_moves_rendered_text_within_local_region() -> None:
    bounds = []
    for alignment in TextAlignment:
        rendered, overflow = ExportService._render_text_box(
            "M\nI",
            160,
            100,
            _font_path(),
            "white",
            fill_color=(0, 0, 0),
            font_size=32,
            text_alignment=alignment,
        )
        assert not overflow
        difference = ImageChops.difference(rendered, Image.new("RGB", rendered.size, "white"))
        text_bounds = difference.getbbox()
        assert text_bounds is not None
        bounds.append(text_bounds)

    assert bounds[0][0] < bounds[1][0] < bounds[2][0]
    assert bounds[0][2] < bounds[1][2] < bounds[2][2]


def test_preview_manual_line_spacing_changes_rendered_height(tmp_path: Path) -> None:
    source = tmp_path / "line-spacing.png"
    Image.new("RGB", (160, 160), "white").save(source)
    bbox = BoundingBox(x=20, y=20, width=120, height=120)

    def render(spacing: int | None, name: str) -> int:
        page = make_page(
            source,
            29 if spacing is None else 30,
            [
                {
                    "bbox": bbox,
                    "translated_text": "ไ\nไ",
                    "typesetting_font_size": 24,
                    "typesetting_line_spacing": spacing,
                }
            ],
        )
        destination = tmp_path / name
        warnings, issues = ExportService.render_page_preview(
            page, tmp_path, destination, _font_path(), clean_background=False
        )
        assert warnings == issues == ()
        with Image.open(destination) as preview:
            difference = ImageChops.difference(
                preview.crop((20, 20, 140, 140)).convert("RGB"),
                Image.new("RGB", (120, 120), "white"),
            )
            bounds = difference.getbbox()
        assert bounds is not None
        return bounds[3] - bounds[1]

    automatic = render(None, "automatic-spacing.png")
    manual = render(24, "manual-spacing.png")
    assert manual > automatic


def test_automatic_thai_line_spacing_preserves_ink_gap_and_fits_font() -> None:
    font = _thai_font_path()
    metrics: list[tuple[int, tuple[int, int, int], tuple[int, int, int] | None, int]] = []
    rendered, overflow = ExportService._render_text_box(
        "เก้าอี้\nเก้าอี้",
        300,
        180,
        font,
        "white",
        fill_color=(0, 0, 0),
        font_size=48,
    )
    rows = [
        y
        for y in range(rendered.height)
        if any(rendered.getpixel((x, y)) != (255, 255, 255) for x in range(rendered.width))
    ]
    line_groups: list[list[int]] = []
    for y in rows:
        if not line_groups or y - line_groups[-1][-1] > 5:
            line_groups.append([y])
        else:
            line_groups[-1].append(y)

    assert not overflow
    assert len(line_groups) == 2
    assert line_groups[1][0] - line_groups[0][-1] - 1 >= 16

    _, overflow = ExportService._render_text_box(
        "เก้าอี้\nเก้าอี้",
        200,
        126,
        font,
        "white",
        fill_color=(0, 0, 0),
        maximum_font_size=48,
        on_metric=lambda *metric: metrics.append(metric),
    )

    assert not overflow
    assert metrics[0][0] < 48


def test_archive_manual_cleanups_targets_blocks_and_keeps_numbered_backups(
    tmp_path: Path,
) -> None:
    page_id = UUID("00000000-0000-0000-0000-000000000001")
    first_id = UUID("00000000-0000-0000-0000-000000000002")
    second_id = UUID("00000000-0000-0000-0000-000000000003")
    cleanup_dir = tmp_path / "cleanups" / f"page-{page_id}"
    cleanup_dir.mkdir(parents=True)
    first = cleanup_dir / f"block-{first_id}.png"
    second = cleanup_dir / f"block-{second_id}.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first.with_name(f"{first.name}.bak").write_bytes(b"older")

    archived = ExportService.archive_manual_cleanups(tmp_path, page_id, (first_id, first_id))

    assert archived == (first.with_name(f"{first.name}.bak2"),)
    assert archived[0].read_bytes() == b"first"
    assert not first.exists()
    assert second.read_bytes() == b"second"
    assert ExportService.archive_manual_cleanups(tmp_path, page_id, (first_id,)) == ()


def test_prepare_manual_cleanups_creates_exact_crop_without_overwriting_edits(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.png"
    source = Image.new("RGB", (160, 120))
    for y in range(source.height):
        for x in range(source.width):
            source.putpixel((x, y), (x, y, (x + y) % 256))
    source.save(source_path)
    page = make_page(
        source_path,
        27,
        [{"bbox": BoundingBox(x=0, y=0, width=20, height=10)}],
    )
    block = page.blocks[0]

    paths = ExportService.prepare_manual_cleanups(page, tmp_path, (block.id,))

    expected_path = ExportService._manual_cleanup_path(tmp_path, page, block)
    assert expected_path.parent.name == f"page-source-{page.id}"
    assert expected_path.name == f"block-001-{block.id}.png"
    assert paths == (expected_path,)
    with Image.open(expected_path) as cleanup:
        assert cleanup.size == (36, 26)
        assert cleanup.tobytes() == source.crop((0, 0, 36, 26)).tobytes()

    Image.new("RGB", (36, 26), "red").save(expected_path)
    ExportService.prepare_manual_cleanups(page, tmp_path, (block.id,))
    with Image.open(expected_path) as cleanup:
        assert cleanup.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.parametrize("invalid_kind", ["unreadable", "wrong-size"])
def test_invalid_manual_cleanup_override_preserves_block_and_reports_issue(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    source = tmp_path / "complex.png"
    original = save_complex_image(source)
    page = make_page(
        source,
        25,
        [{"bbox": BoundingBox(x=30, y=20, width=100, height=80), "translated_text": "ไ"}],
    )
    override_path = tmp_path / "cleanups" / f"page-{page.id}" / f"block-{page.blocks[0].id}.png"
    override_path.parent.mkdir(parents=True)
    if invalid_kind == "unreadable":
        override_path.write_bytes(b"not an image")
    else:
        Image.new("RGB", (131, 112), "white").save(override_path)

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "preview.png",
        _font_path(),
        clean_background=True,
    )

    assert warnings == ()
    assert len(issues) == 1
    assert issues[0].recoverable
    assert issues[0].page_id == page.id
    assert issues[0].block_id == page.blocks[0].id
    assert "invalid manual cleanup override" in issues[0].message
    with Image.open(tmp_path / "preview.png") as preview:
        assert (
            preview.crop((30, 20, 130, 100)).tobytes()
            == original.crop((30, 20, 130, 100)).tobytes()
        )


def test_flat_preview_falls_back_to_solid_ring_median_without_inpainting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "flat.png"
    original = Image.new("RGB", (160, 120), (210, 230, 240))
    draw = ImageDraw.Draw(original)
    draw.text((34, 25), "EN", font=ImageFont.truetype(_font_path(), 18), fill=(20, 30, 40))
    original.save(source)
    destination = tmp_path / "fallback-preview.png"
    page = make_page(
        source,
        21,
        [{"bbox": BoundingBox(x=30, y=20, width=100, height=80), "translated_text": "ไ"}],
    )
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setitem(sys.modules, "numpy", None)

    warnings, issues = ExportService.render_page_preview(
        page,
        tmp_path,
        destination,
        _font_path(),
        clean_background=True,
    )

    assert warnings == issues == ()
    with Image.open(destination) as preview:
        glyph_pixels = [
            (x, y)
            for y in range(25, 45)
            for x in range(34, 65)
            if original.getpixel((x, y)) == (20, 30, 40)
        ]
        assert glyph_pixels
        assert all(preview.getpixel(point) != (20, 30, 40) for point in glyph_pixels)
        assert preview.crop((0, 0, 30, 120)).tobytes() == original.crop((0, 0, 30, 120)).tobytes()


def test_preview_cleanup_never_imports_cv2(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "no-cv2.png"
    save_image(source, "white")
    page = make_page(
        source,
        22,
        [{"bbox": BoundingBox(x=30, y=20, width=100, height=80), "translated_text": "ไ"}],
    )
    imported: list[str] = []
    original_import = builtins.__import__

    def track_import(name, *args, **kwargs):
        imported.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", track_import)
    ExportService.render_page_preview(
        page,
        tmp_path,
        tmp_path / "no-cv2-preview.png",
        _font_path(),
        clean_background=True,
    )

    assert "cv2" not in imported


def test_overlapping_cleanup_does_not_erase_earlier_translation(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    save_image(source, "white")
    first_bbox = BoundingBox(x=20, y=20, width=80, height=80)
    later_bbox = BoundingBox(x=45, y=20, width=80, height=80)
    combined = make_page(
        source,
        16,
        [
            {"bbox": first_bbox, "translated_text": "M"},
            {"bbox": later_bbox, "translated_text": "I"},
        ],
    )
    first_only = make_page(source, 17, [{"bbox": first_bbox, "translated_text": "M"}])
    later_only = make_page(source, 18, [{"bbox": later_bbox, "translated_text": "I"}])
    combined_path = tmp_path / "combined.png"
    first_only_path = tmp_path / "first-only.png"
    later_only_path = tmp_path / "later-only.png"

    ExportService.render_page_preview(
        combined,
        tmp_path,
        combined_path,
        _font_path(),
        clean_background=True,
    )
    ExportService.render_page_preview(
        first_only,
        tmp_path,
        first_only_path,
        _font_path(),
        clean_background=True,
    )
    ExportService.render_page_preview(
        later_only,
        tmp_path,
        later_only_path,
        _font_path(),
        clean_background=True,
    )

    with (
        Image.open(combined_path) as combined_preview,
        Image.open(first_only_path) as first_preview,
        Image.open(later_only_path) as later_preview,
    ):
        candidates = [
            (x, y)
            for y in range(20, 100)
            for x in range(45, 100)
            if first_preview.getpixel((x, y)) != (255, 255, 255)
            and later_preview.getpixel((x, y)) == (255, 255, 255)
        ]
        assert candidates
        assert any(
            combined_preview.getpixel(point) == first_preview.getpixel(point)
            for point in candidates
        )


def test_single_page_preview_rejects_collisions_and_is_atomic(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "page.png"
    original = save_image(source)
    page = make_page(source, 14, [{"translated_text": "แปล"}])

    with pytest.raises(ValueError, match="overwrite a source"):
        ExportService.render_page_preview(page, tmp_path, source, _font_path())
    assert source.read_bytes() == original

    destination = tmp_path / "preview.png"
    destination.write_bytes(b"existing preview")

    def fail_replace(source_path, destination_path):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        ExportService.render_page_preview(page, tmp_path, destination, _font_path())
    assert destination.read_bytes() == b"existing preview"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    ("font_path", "background", "error"),
    [("missing.ttf", "white", FileNotFoundError), ("valid", "invalid", ValueError)],
)
def test_single_page_preview_validates_font_and_background(
    tmp_path: Path,
    font_path: str,
    background: str,
    error: type[Exception],
) -> None:
    source = tmp_path / "page.png"
    save_image(source)
    font = _font_path() if font_path == "valid" else tmp_path / font_path

    with pytest.raises(error):
        ExportService.render_page_preview(
            make_page(source, 15),
            tmp_path,
            tmp_path / "preview.png",
            font,
            background_color=background,
        )

    assert not (tmp_path / "preview.png").exists()


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
    assert result.preview_paths[0].name == "2.png"


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
    source = output / "1.png"
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
    assert (output / "1.png").is_file()
    assert not (output / "2.png").exists()
    assert not list(output.glob(".*.tmp"))
    assert first_path.read_bytes() == first_bytes
    assert second_path.read_bytes() == second_bytes
