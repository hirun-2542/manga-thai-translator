from dataclasses import dataclass
from math import cos, isfinite, radians, sin

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

    if (
        bbox.x >= 0.0
        and bbox.y >= 0.0
        and bbox.x + bbox.width <= image_width
        and bbox.y + bbox.height <= image_height
    ):
        return bbox

    left = max(0.0, bbox.x)
    top = max(0.0, bbox.y)
    right = min(image_width, bbox.x + bbox.width)
    bottom = min(image_height, bbox.y + bbox.height)
    if right <= left or bottom <= top:
        raise ValueError("bounding box has no area inside the image")

    return BoundingBox(x=left, y=top, width=right - left, height=bottom - top)


def rotated_bbox_corners(
    bbox: BoundingBox,
    rotation_degrees: float,
) -> tuple[tuple[float, float], ...]:
    """Return region corners after clockwise image-space rotation around its center."""
    center_x = bbox.x + bbox.width / 2
    center_y = bbox.y + bbox.height / 2
    angle = radians(rotation_degrees)
    cosine = cos(angle)
    sine = sin(angle)
    corners = (
        (bbox.x, bbox.y),
        (bbox.x + bbox.width, bbox.y),
        (bbox.x + bbox.width, bbox.y + bbox.height),
        (bbox.x, bbox.y + bbox.height),
    )
    return tuple(
        (
            center_x + (x - center_x) * cosine - (y - center_y) * sine,
            center_y + (x - center_x) * sine + (y - center_y) * cosine,
        )
        for x, y in corners
    )


def rotated_bbox_bounds(bbox: BoundingBox, rotation_degrees: float) -> BoundingBox:
    """Return the axis-aligned bounds enclosing a rotated region."""
    corners = rotated_bbox_corners(bbox, rotation_degrees)
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return BoundingBox(
        x=min(xs),
        y=min(ys),
        width=max(xs) - min(xs),
        height=max(ys) - min(ys),
    )
