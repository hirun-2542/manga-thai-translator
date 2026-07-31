"""Route resolved source languages to explicit OCR providers."""

from collections.abc import Mapping

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr.base import OcrCapabilities, OcrProvider, OcrResult
from app.services.ocr.registry import OcrProviderRegistry
from app.services.types import ImageInput


class OcrRouter:
    name = "ocr-router"

    def __init__(
        self,
        registry: OcrProviderRegistry,
        routes: Mapping[SourceLanguage, str],
    ) -> None:
        self._registry = registry
        self._routes = {SourceLanguage(language): name for language, name in routes.items()}
        if SourceLanguage.AUTO in self._routes:
            raise ValueError("auto cannot be configured as an OCR route")
        if any(not name or name != name.strip() for name in self._routes.values()):
            raise ValueError("OCR route provider names must be non-empty and trimmed")

    @property
    def capabilities(self) -> OcrCapabilities:
        providers = [self.provider_for(language) for language in self._routes]
        return OcrCapabilities(
            supported_languages=frozenset(self._routes),
            supports_vertical_text=all(
                provider.capabilities.supports_vertical_text for provider in providers
            ),
            supports_cpu=all(provider.capabilities.supports_cpu for provider in providers),
            requires_network=any(provider.capabilities.requires_network for provider in providers),
            uploads_images=any(provider.capabilities.uploads_images for provider in providers),
        )

    def provider_for(self, source_language: SourceLanguage) -> OcrProvider:
        try:
            language = SourceLanguage(source_language)
        except (TypeError, ValueError):
            raise UnsupportedLanguageError(self.name, str(source_language)) from None
        if language is SourceLanguage.AUTO:
            raise UnsupportedLanguageError(self.name, language.value)
        try:
            provider_name = self._routes[language]
        except KeyError:
            raise ProviderError(
                f"no OCR provider route is configured for {language.value!r}",
                recoverable=True,
            ) from None
        try:
            provider = self._registry.get(provider_name)
        except KeyError as error:
            raise ProviderError(str(error.args[0]), recoverable=True) from None
        if language not in provider.capabilities.supported_languages:
            raise ProviderError(
                f"OCR route for {language.value!r} selects {provider_name!r}, "
                "which does not support that language",
                recoverable=True,
            )
        return provider

    def provider_name_for(self, source_language: SourceLanguage) -> str:
        return self.provider_for(source_language).name

    def capabilities_for(self, source_language: SourceLanguage) -> OcrCapabilities:
        return self.provider_for(source_language).capabilities

    async def recognize(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: SourceLanguage,
    ) -> OcrResult:
        provider = self.provider_for(source_language)
        return await provider.recognize(image, bbox, SourceLanguage(source_language))
