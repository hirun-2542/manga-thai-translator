from collections.abc import Iterable

from app.core.models import ReadingOrderPreset, TextBlock


def sort_blocks(
    blocks: Iterable[TextBlock],
    preset: ReadingOrderPreset,
) -> list[TextBlock]:
    preset_value = preset.value
    if preset_value not in {"manga_rtl", "comic_ltr", "webtoon_vertical", "custom"}:
        raise ValueError(f"unsupported reading order preset: {preset!r}")

    def sort_key(block: TextBlock) -> tuple[float | int | str, ...]:
        if preset_value == "manga_rtl":
            return block.bbox.y, -block.bbox.x, str(block.id)
        if preset_value == "comic_ltr":
            return block.bbox.y, block.bbox.x, str(block.id)
        if preset_value == "webtoon_vertical":
            return block.bbox.y, str(block.id)
        return block.reading_order, str(block.id)

    return sorted(blocks, key=sort_key)


def normalize_reading_order(blocks: Iterable[TextBlock]) -> list[TextBlock]:
    return [
        block.model_copy(update={"reading_order": order})
        for order, block in enumerate(blocks, start=1)
    ]
