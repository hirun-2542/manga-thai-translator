from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.models import (
    SCHEMA_VERSION,
    BlockStatus,
    BoundingBox,
    Page,
    Project,
    ProviderConfiguration,
    ReadingOrderPreset,
    SourceLanguage,
    TextAlignment,
    TextBlock,
    TranslationInput,
    WritingMode,
)


def test_public_enums_have_specified_values() -> None:
    assert {item.value for item in SourceLanguage} == {
        "ja",
        "en",
        "ko",
        "zh-Hans",
        "zh-Hant",
        "auto",
    }
    assert {item.value for item in ReadingOrderPreset} == {
        "manga_rtl",
        "comic_ltr",
        "webtoon_vertical",
        "custom",
    }
    assert {item.value for item in WritingMode} >= {"horizontal", "vertical"}
    assert {item.value for item in TextAlignment} == {"left", "center", "right"}
    assert {item.value for item in BlockStatus} == {
        "detected",
        "language_review_required",
        "ocr_complete",
        "ocr_reviewed",
        "translated",
        "translation_reviewed",
        "error",
    }


def test_project_page_and_block_get_distinct_uuid_defaults() -> None:
    page = Page(source_path="page.png", width=100, height=100)
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=0, y=0, width=10, height=10),
        reading_order=0,
    )
    projects = [Project(name="one"), Project(name="two")]

    assert projects[0].id != projects[1].id
    assert page.id != block.id
    assert projects[0].settings.target_language == "th"
    assert block.typesetting_font_size is None
    assert block.typesetting_line_spacing is None
    assert block.typesetting_alignment is TextAlignment.CENTER
    assert block.rotation_degrees == 0.0
    assert block.mirror_horizontal is False
    assert block.mirror_vertical is False
    assert block.typesetting_font_family is None
    assert block.typesetting_font_style is None
    assert block.typesetting_fill_color is None
    assert block.typesetting_stroke_color is None
    assert block.typesetting_stroke_width == 0


def test_project_round_trip_preserves_ids_and_multilingual_text() -> None:
    page_id = uuid4()
    blocks = [
        TextBlock(
            page_id=page_id,
            bbox=BoundingBox(x=1, y=2, width=3, height=4),
            reading_order=index,
            source_language=language,
            source_text=text,
            typesetting_line_spacing=index * 2,
        )
        for index, (language, text) in enumerate(
            [
                (SourceLanguage.JA, "おかえり"),
                (SourceLanguage.EN, "Welcome home"),
                (SourceLanguage.KO, "어서 와"),
                (SourceLanguage.ZH_HANS, "欢迎回来"),
                (SourceLanguage.ZH_HANT, "歡迎回來"),
            ]
        )
    ]
    project = Project(
        name="มังงะ",
        pages=[
            Page(
                id=page_id,
                source_path="ภาพ/หน้า01.png",
                width=1200,
                height=1800,
                blocks=blocks,
            )
        ],
    )

    restored = Project.model_validate_json(project.model_dump_json())

    assert restored == project
    assert restored.id == project.id
    assert [block.id for block in restored.pages[0].blocks] == [block.id for block in blocks]
    assert [block.typesetting_line_spacing for block in restored.pages[0].blocks] == [0, 2, 4, 6, 8]
    assert restored.created_at.utcoffset().total_seconds() == 0


def test_typesetting_overrides_round_trip_and_validate_rgb_values() -> None:
    block = TextBlock(
        page_id=uuid4(),
        bbox=BoundingBox(x=0, y=0, width=10, height=10),
        reading_order=0,
        typesetting_font_family="Noto Sans Thai",
        typesetting_font_style="Bold",
        typesetting_fill_color="#102030",
        typesetting_stroke_color="#A0B0C0",
        typesetting_stroke_width=0,
        typesetting_alignment=TextAlignment.RIGHT,
    )

    restored = TextBlock.model_validate_json(block.model_dump_json())

    assert restored == block
    assert restored.typesetting_alignment is TextAlignment.RIGHT
    for kwargs in (
        {"typesetting_font_style": "Bold"},
        {"typesetting_fill_color": "#12345"},
        {"typesetting_fill_color": "rgba(0, 0, 0, 0.5)"},
        {"typesetting_stroke_width": -1},
        {"typesetting_stroke_width": 33},
        {"typesetting_alignment": "justify"},
    ):
        with pytest.raises(ValidationError):
            TextBlock(
                page_id=uuid4(),
                bbox=BoundingBox(x=0, y=0, width=10, height=10),
                reading_order=0,
                **kwargs,
            )


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (BoundingBox, "x", float("nan")),
        (BoundingBox, "width", 0),
        (Page, "width", 0),
        (TextBlock, "reading_order", -1),
        (TextBlock, "ocr_confidence", 1.01),
        (TextBlock, "typesetting_font_size", 7),
        (TextBlock, "typesetting_font_size", 257),
        (TextBlock, "typesetting_line_spacing", -1),
        (TextBlock, "typesetting_line_spacing", 257),
        (TextBlock, "rotation_degrees", 180.1),
    ],
)
def test_numeric_validation(model: type, field: str, value: object) -> None:
    valid = {
        BoundingBox: dict(x=0, y=0, width=1, height=1),
        Page: dict(source_path="page.png", width=1, height=1),
        TextBlock: dict(
            page_id=uuid4(),
            bbox=BoundingBox(x=0, y=0, width=1, height=1),
            reading_order=0,
        ),
    }[model]
    valid[field] = value

    with pytest.raises(ValidationError):
        model(**valid)


def test_schema_version_and_extra_fields_are_rejected() -> None:
    assert SCHEMA_VERSION == 2
    with pytest.raises(ValidationError):
        Project(name="test", schema_version=1)
    with pytest.raises(ValidationError):
        Project(name="test", api_key="secret")


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Project(name="test", created_at=datetime(2026, 1, 1))


def test_translation_input_rejects_unresolved_language() -> None:
    with pytest.raises(ValidationError):
        TranslationInput(
            id=uuid4(),
            source_language=SourceLanguage.AUTO,
            source_text="text",
            reading_order=0,
        )


def test_provider_configuration_accepts_only_secret_reference() -> None:
    config = ProviderConfiguration(
        provider="openai-compatible",
        model="user-selected-model",
        api_key_env="MANGA_TRANSLATION_API_KEY",
    )
    assert config.api_key_env == "MANGA_TRANSLATION_API_KEY"

    with pytest.raises(ValidationError):
        ProviderConfiguration(
            provider="openai-compatible",
            model="user-selected-model",
            api_key="secret",
        )
