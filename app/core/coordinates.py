from dataclasses import dataclass
from math import isfinite

from app.core.models import BoundingBox


@dataclass(frozen=True, slots=True)
class CoordinateTransform:
    scale: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def __post_init__(self) -> None:
        if not isfinite(self.scale) or self.scale <= 0:
            raise ValueError("scale must be a positive finite number")

    def image_to_scene(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.offset_x, y * self.scale + self.offset_y

    def scene_to_image(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale


def normalize_bbox(x1: float, y1: float, x2: float, y2: float) -> BoundingBox:
    return BoundingBox(
        x=min(x1, x2),
        y=min(y1, y2),
        width=abs(x2 - x1),
        height=abs(y2 - y1),
    )


def clamp_bbox(
    bbox: BoundingBox,
    image_width: float,
    image_height: float,
) -> BoundingBox:
    if (
        not isfinite(image_width)
        or not isfinite(image_height)
        or image_width <= 0
        or image_height <= 0
    ):
        raise ValueError("image dimensions must be positive")

    left = max(0.0, bbox.x)
    top = max(0.0, bbox.y)
    right = min(image_width, bbox.x + bbox.width)
    bottom = min(image_height, bbox.y + bbox.height)
    if right <= left or bottom <= top:
        raise ValueError("bounding box has no area inside the image")

    return BoundingBox(x=left, y=top, width=right - left, height=bottom - top)
