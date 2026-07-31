"""Deterministic text detection for tests and offline development."""

from collections.abc import Iterable

from app.core.models import BoundingBox
from app.services.text_detection.base import DetectedRegion
from app.services.types import ImageInput


class MockTextDetectionProvider:
    """Generate one central region by default or return configured regions.

    Detection providers report image-space boxes; the workflow owns clamping
    custom regions to ``ImageInput.width`` and ``ImageInput.height``.
    """

    def __init__(self, regions: Iterable[DetectedRegion] | None = None) -> None:
        self._regions = None if regions is None else tuple(regions)

    async def detect(self, image: ImageInput) -> list[DetectedRegion]:
        if self._regions is None:
            width = max(1.0, image.width * 0.5)
            height = max(1.0, image.height * 0.2)
            return [
                DetectedRegion(
                    bbox=BoundingBox(
                        x=(image.width - width) / 2,
                        y=(image.height - height) / 2,
                        width=width,
                        height=height,
                    ),
                    confidence=0.9,
                )
            ]
        return list(self._regions)
