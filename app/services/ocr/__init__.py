"""OCR provider API."""

from app.services.ocr.base import OcrCapabilities, OcrProvider, OcrResult
from app.services.ocr.manga_ocr import MangaOcrProvider
from app.services.ocr.mock import MockOcrProvider
from app.services.ocr.paddle_ocr import PaddleOcrProvider
from app.services.ocr.registry import OcrProviderRegistry
from app.services.ocr.router import OcrRouter

__all__ = [
    "MangaOcrProvider",
    "MockOcrProvider",
    "OcrCapabilities",
    "OcrProvider",
    "OcrProviderRegistry",
    "OcrResult",
    "OcrRouter",
    "PaddleOcrProvider",
]
