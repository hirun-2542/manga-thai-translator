"""Lazy local PaddleOCR 3.x adapter."""

import asyncio
import json
from collections.abc import Callable, Mapping
from importlib import import_module
from math import isfinite
from threading import Lock
from typing import Any

from PIL import Image

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr._image import load_crop
from app.services.ocr.base import OcrCapabilities, OcrResult
from app.services.types import ImageInput

type PaddleOcrFactory = Callable[..., Any]

_LANGUAGES = {
    SourceLanguage.EN: "en",
    SourceLanguage.KO: "korean",
    SourceLanguage.ZH_HANS: "ch",
    SourceLanguage.ZH_HANT: "chinese_cht",
}


class PaddleOcrProvider:
    name = "paddleocr"

    def __init__(
        self,
        *,
        device: str = "cpu",
        model_factory: PaddleOcrFactory | None = None,
    ) -> None:
        self._device = device
        self._model_factory = model_factory
        self._models: dict[SourceLanguage, Any] = {}
        self._model_lock = Lock()
        self._capabilities = OcrCapabilities(
            supported_languages=frozenset(_LANGUAGES),
            supports_vertical_text=False,
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
        if language not in _LANGUAGES:
            raise UnsupportedLanguageError(self.name, str(source_language))
        try:
            raw = await asyncio.to_thread(self._recognize_sync, image, bbox, language)
            text, confidence = _parse_result(raw)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(f"{self.name} OCR failed: {error}", recoverable=True) from error
        return OcrResult(
            source_text=text,
            confidence=confidence,
            provider=self.name,
            detected_language=language,
        )

    def _recognize_sync(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: SourceLanguage,
    ) -> object:
        crop = load_crop(image, bbox)
        model_input = crop if self._model_factory is not None else _as_array(crop)
        return self._model_instance(source_language).predict(model_input)

    def _model_instance(self, source_language: SourceLanguage) -> Any:
        with self._model_lock:
            if source_language not in self._models:
                try:
                    factory = self._model_factory or self._default_factory()
                    self._models[source_language] = factory(
                        lang=_LANGUAGES[source_language],
                        device=self._device,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
                except (ImportError, ModuleNotFoundError) as error:
                    raise ProviderError(
                        "PaddleOCR is unavailable; install the 'ocr' optional extra",
                        recoverable=True,
                    ) from error
                except Exception as error:
                    raise ProviderError(
                        f"PaddleOCR initialization failed: {error}",
                        recoverable=True,
                    ) from error
            return self._models[source_language]

    @staticmethod
    def _default_factory() -> PaddleOcrFactory:
        from paddleocr import PaddleOCR

        return PaddleOCR


def _as_array(image: Image.Image) -> object:
    try:
        return import_module("numpy").asarray(image)
    except (ImportError, ModuleNotFoundError) as error:
        raise ProviderError(
            "PaddleOCR image conversion is unavailable; install the 'ocr' optional extra",
            recoverable=True,
        ) from error


def _parse_result(raw: object) -> tuple[str, float]:
    entries = [raw] if _result_fields(raw) is not None else _as_entries(raw)
    texts: list[str] = []
    scores: list[float] = []
    for entry in entries:
        fields = _result_fields(entry)
        if fields is None:
            raise ProviderError("PaddleOCR returned a malformed result", recoverable=True)
        raw_texts, raw_scores = fields
        raw_texts = _as_values(raw_texts)
        raw_scores = _as_values(raw_scores)
        if raw_texts is None or raw_scores is None or len(raw_texts) != len(raw_scores):
            raise ProviderError("PaddleOCR returned a malformed result", recoverable=True)
        for text, score in zip(raw_texts, raw_scores, strict=True):
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("PaddleOCR returned empty text", recoverable=True)
            try:
                numeric_score = float(score)
            except (TypeError, ValueError):
                raise ProviderError(
                    "PaddleOCR returned an invalid confidence", recoverable=True
                ) from None
            if not isfinite(numeric_score):
                raise ProviderError("PaddleOCR returned an invalid confidence", recoverable=True)
            texts.append(text)
            scores.append(min(1.0, max(0.0, numeric_score)))
    if not texts:
        raise ProviderError("PaddleOCR returned no recognized text", recoverable=True)
    return "\n".join(texts), sum(scores) / len(scores)


def _as_values(value: object) -> list[object] | None:
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _as_entries(raw: object) -> list[object]:
    if isinstance(raw, (str, bytes, Mapping)):
        raise ProviderError("PaddleOCR returned a malformed result", recoverable=True)
    try:
        return list(raw)  # type: ignore[arg-type]
    except TypeError:
        raise ProviderError("PaddleOCR returned a malformed result", recoverable=True) from None


def _result_fields(result: object) -> tuple[object, object] | None:
    if hasattr(result, "rec_texts") or hasattr(result, "rec_scores"):
        return getattr(result, "rec_texts", None), getattr(result, "rec_scores", None)
    value: object = result
    if not isinstance(value, Mapping) and hasattr(value, "json"):
        value = value.json() if callable(value.json) else value.json
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, Mapping) and isinstance(value.get("res"), Mapping):
        value = value["res"]
    if isinstance(value, Mapping) and ("rec_texts" in value or "rec_scores" in value):
        return value.get("rec_texts"), value.get("rec_scores")
    return None
