from uuid import UUID

from app.core.models import BoundingBox, ReadingOrderPreset, TextBlock
from app.core.reading_order import normalize_reading_order, sort_blocks

PAGE_ID = UUID(int=100)


def block(
    identifier: int,
    *,
    x: float,
    y: float,
    reading_order: int = 0,
) -> TextBlock:
    return TextBlock(
        id=UUID(int=identifier),
        page_id=PAGE_ID,
        bbox=BoundingBox(x=x, y=y, width=10, height=10),
        reading_order=reading_order,
    )


def test_sort_blocks_for_manga_rtl_is_top_then_right() -> None:
    blocks = [
        block(1, x=10, y=20),
        block(2, x=10, y=5),
        block(3, x=80, y=5),
    ]

    assert [item.id for item in sort_blocks(blocks, ReadingOrderPreset.MANGA_RTL)] == [
        UUID(int=3),
        UUID(int=2),
        UUID(int=1),
    ]


def test_sort_blocks_for_comic_ltr_is_top_then_left() -> None:
    blocks = [
        block(1, x=10, y=20),
        block(2, x=80, y=5),
        block(3, x=10, y=5),
    ]

    assert [item.id for item in sort_blocks(blocks, ReadingOrderPreset.COMIC_LTR)] == [
        UUID(int=3),
        UUID(int=2),
        UUID(int=1),
    ]


def test_webtoon_and_custom_ties_use_stable_uuid() -> None:
    high_id = block(20, x=80, y=5, reading_order=2)
    low_id = block(10, x=10, y=5, reading_order=2)

    assert sort_blocks(
        [high_id, low_id],
        ReadingOrderPreset.WEBTOON_VERTICAL,
    ) == [low_id, high_id]
    assert sort_blocks([high_id, low_id], ReadingOrderPreset.CUSTOM) == [
        low_id,
        high_id,
    ]


def test_normalize_reading_order_copies_blocks_without_changing_ids_or_inputs() -> None:
    blocks = [
        block(1, x=0, y=0, reading_order=8),
        block(2, x=0, y=20, reading_order=3),
    ]

    normalized = normalize_reading_order(blocks)

    assert [item.reading_order for item in normalized] == [1, 2]
    assert [item.id for item in normalized] == [item.id for item in blocks]
    assert [item.reading_order for item in blocks] == [8, 3]
    assert all(copy is not original for copy, original in zip(normalized, blocks, strict=True))
