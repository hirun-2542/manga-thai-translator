import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr import (
    OcrCapabilities,
    OcrProviderRegistry,
    OcrResult,
    OcrRouter,
)
from app.services.types import ImageInput

IMAGE = ImageInput(path=Path("unused.png"), width=10, height=10)
BBOX = BoundingBox(x=0, y=0, width=5, height=5)


class StubProvider:
    def __init__(self, name: str, languages: frozenset[SourceLanguage]) -> None:
        self.name = name
        self.capabilities = OcrCapabilities(
            supported_languages=languages,
            supports_vertical_text=True,
            supports_cpu=True,
            requires_network=False,
            uploads_images=False,
        )
        self.seen: list[SourceLanguage] = []

    async def recognize(self, image, bbox, source_language):
        self.seen.append(source_language)
        return OcrResult(
            source_text=f"text-{source_language.value}",
            confidence=0.8,
            provider=self.name,
            detected_language=source_language,
        )


@pytest.mark.parametrize(
    "languages",
    [frozenset(), frozenset({SourceLanguage.AUTO})],
)
def test_capabilities_require_nonempty_concrete_languages(languages) -> None:
    with pytest.raises(ValidationError, match="supported_languages"):
        OcrCapabilities(
            supported_languages=languages,
            supports_vertical_text=False,
            supports_cpu=True,
            requires_network=False,
            uploads_images=False,
        )


def test_registry_validates_names_and_exposes_sorted_providers() -> None:
    later = StubProvider("z-provider", frozenset({SourceLanguage.JA}))
    earlier = StubProvider("a-provider", frozenset({SourceLanguage.EN}))
    registry = OcrProviderRegistry([later, earlier])

    assert registry.names == ("a-provider", "z-provider")
    assert registry.providers == (earlier, later)
    assert registry.get("z-provider") is later

    with pytest.raises(ValueError, match="already registered"):
        registry.register(later.name, later)
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("", later)
    with pytest.raises(ValueError, match="does not match"):
        OcrProviderRegistry().register("wrong", later)

    later.capabilities = object()
    with pytest.raises(TypeError, match="invalid capabilities"):
        OcrProviderRegistry().register(later.name, later)


def test_router_routes_every_concrete_language_and_exposes_selection() -> None:
    japanese = StubProvider("japanese", frozenset({SourceLanguage.JA}))
    multilingual = StubProvider(
        "multilingual",
        frozenset(
            {
                SourceLanguage.EN,
                SourceLanguage.KO,
                SourceLanguage.ZH_HANS,
                SourceLanguage.ZH_HANT,
            }
        ),
    )
    router = OcrRouter(
        OcrProviderRegistry([multilingual, japanese]),
        {
            SourceLanguage.JA: japanese.name,
            SourceLanguage.EN: multilingual.name,
            SourceLanguage.KO: multilingual.name,
            SourceLanguage.ZH_HANS: multilingual.name,
            SourceLanguage.ZH_HANT: multilingual.name,
        },
    )

    results = [
        asyncio.run(router.recognize(IMAGE, BBOX, language))
        for language in (
            SourceLanguage.JA,
            SourceLanguage.EN,
            SourceLanguage.KO,
            SourceLanguage.ZH_HANS,
            SourceLanguage.ZH_HANT,
        )
    ]

    assert [result.source_text for result in results] == [
        "text-ja",
        "text-en",
        "text-ko",
        "text-zh-Hans",
        "text-zh-Hant",
    ]
    assert router.provider_name_for(SourceLanguage.JA) == japanese.name
    assert router.provider_name_for(SourceLanguage.KO) == multilingual.name
    assert router.capabilities_for(SourceLanguage.EN) is multilingual.capabilities
    assert router.capabilities.supported_languages == frozenset(
        {
            SourceLanguage.JA,
            SourceLanguage.EN,
            SourceLanguage.KO,
            SourceLanguage.ZH_HANS,
            SourceLanguage.ZH_HANT,
        }
    )


def test_router_rejects_auto_missing_and_unsupported_routes_without_fallback() -> None:
    english = StubProvider("english", frozenset({SourceLanguage.EN}))
    registry = OcrProviderRegistry([english])

    with pytest.raises(ValueError, match="auto"):
        OcrRouter(registry, {SourceLanguage.AUTO: english.name})

    missing_route = OcrRouter(registry, {SourceLanguage.EN: english.name})
    with pytest.raises(UnsupportedLanguageError, match="auto") as auto_error:
        missing_route.provider_for(SourceLanguage.AUTO)
    assert auto_error.value.recoverable is True
    with pytest.raises(ProviderError, match="no OCR provider route") as route_error:
        missing_route.provider_for(SourceLanguage.KO)
    assert route_error.value.recoverable is True

    missing_provider = OcrRouter(registry, {SourceLanguage.KO: "missing"})
    with pytest.raises(ProviderError, match="not registered"):
        missing_provider.provider_for(SourceLanguage.KO)

    unsupported = OcrRouter(registry, {SourceLanguage.KO: english.name})
    with pytest.raises(ProviderError, match="does not support"):
        unsupported.provider_for(SourceLanguage.KO)
