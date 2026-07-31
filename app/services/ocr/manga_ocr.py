"""Lazy local manga-ocr adapter."""

import asyncio
from collections.abc import Callable
from threading import Lock
from typing import Any

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr._image import load_crop
from app.services.ocr.base import OcrCapabilities, OcrResult
from app.services.types import ImageInput

type MangaOcrFactory = Callable[..., Any]


class MangaOcrProvider:
    name = "manga-ocr"

    def __init__(
        self,
        *,
        force_cpu: bool = True,
        model_factory: MangaOcrFactory | None = None,
    ) -> None:
        self._force_cpu = force_cpu
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = Lock()
        self._capabilities = OcrCapabilities(
            supported_languages=frozenset({SourceLanguage.JA}),
            supports_vertical_text=True,
            supports_cpu=True,
            requires_network=True,
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
        except (TypeError, ValueError):
            raise UnsupportedLanguageError(self.name, str(source_language)) from None
        if language is not SourceLanguage.JA:
            raise UnsupportedLanguageError(self.name, str(source_language))
        try:
            text = await asyncio.to_thread(self._recognize_sync, image, bbox)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(f"{self.name} OCR failed: {error}", recoverable=True) from error
        if not isinstance(text, str):
            raise ProviderError(f"{self.name} returned non-text output", recoverable=True)
        return OcrResult(
            source_text=text,
            # manga-ocr exposes no confidence; 0.0 is the explicit unknown sentinel.
            confidence=0.0,
            provider=self.name,
            detected_language=language,
        )

    def _recognize_sync(self, image: ImageInput, bbox: BoundingBox) -> object:
        crop = load_crop(image, bbox)
        return self._model_instance()(crop)

    def _model_instance(self) -> Any:
        with self._model_lock:
            if self._model is None:
                try:
                    factory = self._model_factory or self._default_factory()
                    self._model = factory(force_cpu=self._force_cpu)
                except (ImportError, ModuleNotFoundError) as error:
                    raise ProviderError(
                        "manga-ocr is unavailable; install the 'ocr' optional extra",
                        recoverable=True,
                    ) from error
                except Exception as error:
                    raise ProviderError(
                        f"manga-ocr initialization failed: {error}",
                        recoverable=True,
                    ) from error
            return self._model

    @staticmethod
    def _default_factory() -> MangaOcrFactory:
        from manga_ocr import MangaOcr

        return MangaOcr
