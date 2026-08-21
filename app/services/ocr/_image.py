"""Shared immutable source-image cropping."""

from math import ceil, floor

from PIL import Image, UnidentifiedImageError

from app.core.coordinates import clamp_bbox
from app.core.models import BoundingBox
from app.services.types import ImageInput


def load_crop(image: ImageInput, bbox: BoundingBox) -> Image.Image:
    if not image.path.is_file():
        raise ValueError(f"image not found: {image.path}")
    try:
        with Image.open(image.path) as source:
            source.load()
            clamped = clamp_bbox(bbox, *source.size)
            box = (
                floor(clamped.x),
                floor(clamped.y),
                ceil(clamped.x + clamped.width),
                ceil(clamped.y + clamped.height),
            )
            return source.crop(box).copy()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError(f"cannot crop image {image.path}: {error}") from error


def load_rotated_crop(
    image: ImageInput,
    bbox: BoundingBox,
    rotation_degrees: float,
) -> Image.Image:
    """Return a rotated region unwrapped into its local, axis-aligned coordinates."""
    if not image.path.is_file():
        raise ValueError(f"image not found: {image.path}")
    try:
        with Image.open(image.path) as source:
            source.load()
            clamped = clamp_bbox(bbox, *source.size)
            center = (clamped.x + clamped.width / 2, clamped.y + clamped.height / 2)
            upright = source.rotate(
                rotation_degrees,
                resample=Image.Resampling.BICUBIC,
                center=center,
            )
            box = (
                floor(clamped.x),
                floor(clamped.y),
                ceil(clamped.x + clamped.width),
                ceil(clamped.y + clamped.height),
            )
            return upright.crop(box).copy()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError(f"cannot crop rotated image {image.path}: {error}") from error
