"""Lazy local PaddleOCR 3.x adapter."""

import asyncio
import json
import subprocess
import sys
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
from app.services.text_detection import DetectedRegion
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
        self._child_process: subprocess.Popen[str] | None = None
        self._child_state_lock = Lock()
        self._child_request_lock = Lock()
        self._closed = False
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

    def close(self) -> None:
        """Stop the optional Paddle child process; safe to call repeatedly."""
        if self._model_factory is not None:
            return
        with self._child_state_lock:
            if self._closed:
                return
            self._closed = True
            process = self._child_process
            self._child_process = None
        self._stop_process(process)

    async def detect(self, image: ImageInput) -> list[DetectedRegion]:
        try:
            raw = await asyncio.to_thread(self._detect_sync, image)
            return _parse_detections(raw)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                f"{self.name} detection failed: {error}", recoverable=True
            ) from error

    def _detect_sync(self, image: ImageInput) -> object:
        if self._model_factory is None:
            load_crop(image, BoundingBox(x=0, y=0, width=image.width, height=image.height))
            return self._child_request(
                {
                    "operation": "detect",
                    "image_path": str(image.path),
                    "language": _LANGUAGES[SourceLanguage.EN],
                    "device": self._device,
                }
            )
        source = load_crop(image, BoundingBox(x=0, y=0, width=image.width, height=image.height))
        return self._model_instance(SourceLanguage.EN).predict(source)

    def _recognize_sync(
        self,
        image: ImageInput,
        bbox: BoundingBox,
        source_language: SourceLanguage,
    ) -> object:
        crop = load_crop(image, bbox)
        if self._model_factory is None:
            return self._child_request(
                {
                    "operation": "recognize",
                    "image_path": str(image.path),
                    "bbox": bbox.model_dump(mode="json"),
                    "language": _LANGUAGES[source_language],
                    "device": self._device,
                }
            )
        return self._model_instance(source_language).predict(crop)

    def _model_instance(self, source_language: SourceLanguage) -> Any:
        with self._model_lock:
            if source_language not in self._models:
                try:
                    factory = self._model_factory
                    assert factory is not None
                    self._models[source_language] = factory(
                        lang=_LANGUAGES[source_language],
                        device=self._device,
                        enable_mkldnn=False,
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

    def _child_request(self, request: dict[str, object]) -> object:
        with self._child_request_lock:
            process = self._child()
            try:
                stdin = process.stdin
                stdout = process.stdout
                if stdin is None or stdout is None:
                    raise ProviderError("PaddleOCR child has no JSON pipes", recoverable=True)
                stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
                stdin.flush()
                line = stdout.readline()
                if not line:
                    raise ProviderError(
                        "PaddleOCR child exited before returning a response",
                        recoverable=True,
                    )
                response = json.loads(line)
                if not isinstance(response, Mapping) or response.get("ok") is not True:
                    message = (
                        response.get("error", "unknown child error")
                        if isinstance(response, Mapping)
                        else "malformed child response"
                    )
                    raise ProviderError(f"PaddleOCR child failed: {message}", recoverable=True)
                return response.get("result")
            except ProviderError:
                self._discard_child(process)
                raise
            except (BrokenPipeError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                self._discard_child(process)
                raise ProviderError(
                    f"PaddleOCR child communication failed: {error}", recoverable=True
                ) from error

    def _child(self) -> subprocess.Popen[str]:
        with self._child_state_lock:
            if self._closed:
                raise ProviderError("PaddleOCR provider is closed", recoverable=True)
            process = self._child_process
            if process is None or process.poll() is not None:
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "app.services.ocr.paddle_worker"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        bufsize=1,
                    )
                except OSError as error:
                    raise ProviderError(
                        f"PaddleOCR child could not start: {error}", recoverable=True
                    ) from error
                self._child_process = process
            return process

    def _discard_child(self, process: subprocess.Popen[str]) -> None:
        with self._child_state_lock:
            if self._child_process is process:
                self._child_process = None
        self._stop_process(process)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str] | None) -> None:
        if process is None:
            return
        stdin = process.stdin
        if stdin is not None:
            close = getattr(stdin, "close", None)
            if callable(close):
                close()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _as_array(image: Image.Image) -> object:
    try:
        return import_module("numpy").asarray(image.convert("RGB"))
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


def _parse_detections(raw: object) -> list[DetectedRegion]:
    entries = [raw] if _detection_fields(raw) is not None else _as_entries(raw)
    regions = []
    for entry in entries:
        fields = _detection_fields(entry)
        if fields is None:
            raise ProviderError("PaddleOCR returned malformed detections", recoverable=True)
        raw_boxes, raw_scores = map(_as_values, fields)
        if raw_boxes is None or raw_scores is None or len(raw_boxes) != len(raw_scores):
            raise ProviderError("PaddleOCR returned malformed detections", recoverable=True)
        for raw_box, raw_score in zip(raw_boxes, raw_scores, strict=True):
            values = _as_values(raw_box)
            if values is None or len(values) != 4:
                raise ProviderError("PaddleOCR returned an invalid detection box", recoverable=True)
            try:
                x1, y1, x2, y2 = (float(value) for value in values)
                score = float(raw_score)
            except (TypeError, ValueError):
                raise ProviderError(
                    "PaddleOCR returned malformed detections", recoverable=True
                ) from None
            if not all(isfinite(value) for value in (x1, y1, x2, y2, score)):
                raise ProviderError("PaddleOCR returned malformed detections", recoverable=True)
            if x2 <= x1 or y2 <= y1:
                raise ProviderError("PaddleOCR returned an invalid detection box", recoverable=True)
            regions.append(
                DetectedRegion(
                    bbox=BoundingBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1),
                    confidence=min(1.0, max(0.0, score)),
                )
            )
    return _group_lines(regions)


def _group_lines(regions: list[DetectedRegion]) -> list[DetectedRegion]:
    groups: list[list[DetectedRegion]] = []
    # ponytail: gap/center grouping cannot see bubble borders; replace with segmentation when needed.
    for region in sorted(regions, key=lambda item: (item.bbox.y, item.bbox.x)):
        candidates = []
        center = region.bbox.x + region.bbox.width / 2
        for index, group in enumerate(groups):
            previous = group[-1].bbox
            gap = region.bbox.y - (previous.y + previous.height)
            center_distance = abs(center - (previous.x + previous.width / 2))
            line_height = min(region.bbox.height, previous.height)
            if gap <= line_height * 0.75 and center_distance <= line_height * 1.5:
                candidates.append((abs(gap), center_distance, index))
        if candidates:
            groups[min(candidates)[2]].append(region)
        else:
            groups.append([region])

    merged = []
    for group in groups:
        left = min(region.bbox.x for region in group)
        top = min(region.bbox.y for region in group)
        right = max(region.bbox.x + region.bbox.width for region in group)
        bottom = max(region.bbox.y + region.bbox.height for region in group)
        merged.append(
            DetectedRegion(
                bbox=BoundingBox(x=left, y=top, width=right - left, height=bottom - top),
                confidence=sum(region.confidence for region in group) / len(group),
            )
        )
    return merged


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


def _detection_fields(result: object) -> tuple[object, object] | None:
    if hasattr(result, "rec_boxes") or hasattr(result, "rec_scores"):
        return getattr(result, "rec_boxes", None), getattr(result, "rec_scores", None)
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
    if isinstance(value, Mapping) and ("rec_boxes" in value or "rec_scores" in value):
        return value.get("rec_boxes"), value.get("rec_scores")
    return None
