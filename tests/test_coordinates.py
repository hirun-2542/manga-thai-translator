from dataclasses import FrozenInstanceError

import pytest

from app.core.coordinates import CoordinateTransform, clamp_bbox, normalize_bbox
from app.core.models import BoundingBox


def test_coordinate_transform_round_trip_with_scale_and_offsets() -> None:
    transform = CoordinateTransform(scale=2.5, offset_x=10, offset_y=-5)

    scene_point = transform.image_to_scene(4, 8)

    assert scene_point == (20, 15)
    assert transform.scene_to_image(*scene_point) == (4, 8)
    with pytest.raises(FrozenInstanceError):
        transform.scale = 3  # type: ignore[misc]


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_coordinate_transform_rejects_non_positive_or_non_finite_scale(scale: float) -> None:
    with pytest.raises(ValueError, match="scale"):
        CoordinateTransform(scale=scale)


def test_normalize_bbox_supports_reverse_drag() -> None:
    assert normalize_bbox(30, 40, 10, 15) == BoundingBox(
        x=10,
        y=15,
        width=20,
        height=25,
    )


def test_clamp_bbox_uses_image_coordinates() -> None:
    bbox = BoundingBox(x=-10, y=80, width=50, height=40)

    assert clamp_bbox(bbox, image_width=100, image_height=100) == BoundingBox(
        x=0,
        y=80,
        width=40,
        height=20,
    )


def test_clamp_bbox_rejects_box_outside_image() -> None:
    with pytest.raises(ValueError, match="no area"):
        clamp_bbox(
            BoundingBox(x=101, y=20, width=10, height=10),
            image_width=100,
            image_height=100,
        )
