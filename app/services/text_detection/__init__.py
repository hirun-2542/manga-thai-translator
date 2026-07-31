"""Text detection provider API."""

from app.services.text_detection.base import DetectedRegion, TextDetectionProvider
from app.services.text_detection.mock import MockTextDetectionProvider

__all__ = ["DetectedRegion", "MockTextDetectionProvider", "TextDetectionProvider"]
