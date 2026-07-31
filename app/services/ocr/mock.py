"""Deterministic offline OCR provider."""

from collections.abc import Mapping

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import UnsupportedLanguageError
from app.services.ocr.base import OcrCapabilities, OcrResult
from app.services.types import ImageInput

_SUPPORTED_LANGUAGES = frozenset(
    {
        SourceLanguage.JA,
        SourceLanguage.EN,
        SourceLanguage.KO,
        SourceLanguage.ZH_HANS,
        SourceLanguage.ZH_HANT,
    }
)


class MockOcrProvider:
    name = "mock-ocr"

    def __init__(
        self,
        texts: Mapping[SourceLanguage, str] | None = None,
        *,
        confidence: float = 0.99,
    ) -> None:
        configured = {
            SourceLanguage.JA: "おかえり",
            SourceLanguage.EN: "Welcome home",
            SourceLanguage.KO: "어서 와",
            SourceLanguage.ZH_HANS: "欢迎回来",
            SourceLanguage.ZH_HANT: "歡迎回來",
        }
        if texts is not None:
            configured.update(texts)
        self._texts = configured
        self._confidence = confidence
        self._capabilities = OcrCapabilities(
            supported_languages=_SUPPORTED_LANGUAGES,
            supports_vertical_text=True,
            supports_cpu=True,
            requires_network=False,
            uploads_images=False,
        )

    @property
    def capabilities(self) -> OcrCapabilities:
        return self._capabilities

    async def recognize(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: SourceLanguage,
    ) -> OcrResult:
        try:
            language = SourceLanguage(source_language)
            if language is SourceLanguage.AUTO:
                raise ValueError
            source_text = self._texts[language]
        except (KeyError, ValueError):
            raise UnsupportedLanguageError(self.name, str(source_language)) from None

        return OcrResult(
            source_text=source_text,
            confidence=self._confidence,
            provider=self.name,
            detected_language=language,
        )
