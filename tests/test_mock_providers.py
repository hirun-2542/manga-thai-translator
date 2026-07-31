import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.models import (
    BoundingBox,
    SourceLanguage,
    TranslationContext,
    TranslationInput,
)
from app.services import ImageInput, UnsupportedLanguageError
from app.services.ocr import MockOcrProvider
from app.services.text_detection import DetectedRegion, MockTextDetectionProvider
from app.services.translation import MockTranslationProvider

IMAGE = ImageInput(path=Path("หน้า.png"), width=100, height=200)
BBOX = BoundingBox(x=10, y=20, width=30, height=40)
CONCRETE_LANGUAGES = (
    SourceLanguage.JA,
    SourceLanguage.EN,
    SourceLanguage.KO,
    SourceLanguage.ZH_HANS,
    SourceLanguage.ZH_HANT,
)


def test_mock_detection_generates_one_central_region_by_default() -> None:
    provider = MockTextDetectionProvider()
    expected = [
        DetectedRegion(
            bbox=BoundingBox(x=25, y=80, width=50, height=40),
            confidence=0.9,
        )
    ]

    assert asyncio.run(provider.detect(IMAGE)) == expected
    assert asyncio.run(provider.detect(IMAGE)) == expected
    tiny = ImageInput(path=Path("tiny.png"), width=1, height=1)
    assert asyncio.run(provider.detect(tiny))[0].bbox == BoundingBox(
        x=0,
        y=0,
        width=1,
        height=1,
    )


def test_mock_detection_accepts_explicit_empty_regions() -> None:
    assert asyncio.run(MockTextDetectionProvider([]).detect(IMAGE)) == []


def test_mock_detection_returns_configured_regions_without_clamping() -> None:
    regions = [
        DetectedRegion(bbox=BBOX, confidence=0.9),
        DetectedRegion(
            bbox=BoundingBox(x=90, y=190, width=20, height=20),
            confidence=0.5,
        ),
    ]
    provider = MockTextDetectionProvider(regions)

    assert asyncio.run(provider.detect(IMAGE)) == regions
    assert asyncio.run(provider.detect(IMAGE)) == regions


def test_mock_ocr_capabilities_are_offline_cpu_only_and_cover_all_languages() -> None:
    capabilities = MockOcrProvider().capabilities

    assert capabilities.supported_languages == frozenset(CONCRETE_LANGUAGES)
    assert capabilities.supports_vertical_text is True
    assert capabilities.supports_cpu is True
    assert capabilities.requires_network is False
    assert capabilities.uploads_images is False


def test_mock_ocr_is_deterministic_and_configurable_for_every_language() -> None:
    texts = {language: f"text-{language.value}" for language in CONCRETE_LANGUAGES}
    provider = MockOcrProvider(texts, confidence=0.75)

    for language in CONCRETE_LANGUAGES:
        result = asyncio.run(provider.recognize(IMAGE, BBOX, language))
        assert result.source_text == texts[language]
        assert result.confidence == 0.75
        assert result.provider == provider.name
        assert result.detected_language is language


def test_mock_ocr_rejects_unresolved_auto_as_recoverable() -> None:
    with pytest.raises(UnsupportedLanguageError) as error:
        asyncio.run(MockOcrProvider().recognize(IMAGE, BBOX, SourceLanguage.AUTO))

    assert error.value.recoverable is True
    assert error.value.language == SourceLanguage.AUTO.value


def test_translation_input_rejects_auto() -> None:
    with pytest.raises(ValidationError, match="must be resolved"):
        TranslationInput(id=uuid4(), source_language=SourceLanguage.AUTO, source_text="text")


def test_mock_translation_preserves_mixed_language_ids_order_and_empty_source() -> None:
    blocks = [
        TranslationInput(id=uuid4(), source_language=language, source_text=f"text-{index}")
        for index, language in enumerate(CONCRETE_LANGUAGES)
    ]
    blocks.insert(
        2,
        TranslationInput(id=uuid4(), source_language=SourceLanguage.KO, source_text=""),
    )
    context = TranslationContext(default_source_language=SourceLanguage.AUTO)

    results = asyncio.run(MockTranslationProvider().translate_blocks(blocks, context))

    assert [result.id for result in results] == [block.id for block in blocks]
    assert results[2].translated_text == ""
    assert [result.translated_text for result in results if result.translated_text] == [
        f"แปลไทย: {block.source_text}" for block in blocks if block.source_text
    ]
