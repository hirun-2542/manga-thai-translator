from uuid import UUID

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from app.core.models import BlockStatus, BoundingBox, Page, TextBlock
from app.ui.block_strip import BlockStrip


def save_image(path) -> None:
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(path))


def make_block(page_id, block_id: UUID, order: int, text: str, **bbox) -> TextBlock:
    return TextBlock(
        id=block_id,
        page_id=page_id,
        bbox=BoundingBox(**bbox),
        reading_order=order,
        source_text=text,
    )


def test_creates_sorted_compact_rows_for_all_blocks(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    empty = make_block(page.id, UUID(int=10), 0, "", x=0, y=0, width=20, height=20)
    later = make_block(page.id, UUID(int=20), 2, "later", x=0, y=0, width=20, height=20)
    tied_later_id = make_block(
        page.id, UUID(int=4), 1, "first tie", x=10, y=10, width=40, height=20
    )
    tied_earlier_id = make_block(
        page.id, UUID(int=3), 1, "second tie", x=10, y=10, width=40, height=20
    )
    page.blocks = [later, empty, tied_later_id, tied_earlier_id]

    strip = BlockStrip()
    strip.set_page(page)

    assert list(strip._cards) == [empty.id, tied_earlier_id.id, tied_later_id.id, later.id]
    assert len(strip._cards) == 4
    thumbnail = strip._cards[tied_earlier_id.id].thumbnail_label.pixmap()
    assert thumbnail is not None and not thumbnail.isNull()
    assert thumbnail.size().width() == 72
    assert strip._cards[tied_earlier_id.id].source_label.text() == "second tie"
    assert strip._cards[empty.id].source_label.maximumHeight() == 36


def test_status_is_exposed_as_text_and_icon(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    block = make_block(page.id, UUID(int=1), 1, "hello", x=5, y=5, width=20, height=20)
    block = block.model_copy(update={"status": BlockStatus.ERROR})
    page.blocks = [block]
    strip = BlockStrip()

    strip.set_page(page)

    assert "Error" in strip._cards[block.id].status_label.text()
    assert "⚠" in strip._cards[block.id].status_label.text()


def test_card_click_emits_selection_and_highlights_card(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    block = make_block(page.id, UUID(int=1), 1, "hello", x=5, y=5, width=20, height=20)
    page.blocks = [block]
    strip = BlockStrip()
    selected = []
    strip.block_selected.connect(selected.append)
    strip.set_page(page)
    strip.show()
    qapp.processEvents()

    QTest.mouseClick(strip._cards[block.id], Qt.MouseButton.LeftButton)

    assert selected == [block.id]
    assert strip._cards[block.id].property("selected") is True


def test_ctrl_click_toggles_arbitrary_subset_and_keeps_recent_primary(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    first = make_block(page.id, UUID(int=1), 2, "first", x=5, y=5, width=20, height=20)
    second = make_block(page.id, UUID(int=2), 1, "second", x=35, y=5, width=20, height=20)
    third = make_block(page.id, UUID(int=3), 3, "third", x=65, y=5, width=20, height=20)
    page.blocks = [first, second, third]
    strip = BlockStrip()
    states = []
    strip.selection_changed.connect(states.append)
    strip.set_page(page)
    strip.show()
    qapp.processEvents()

    QTest.mouseClick(strip._cards[second.id], Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        strip._cards[third.id],
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert strip.selected_block_ids == (second.id, third.id)
    assert strip._selected_id == third.id
    assert states[-1] == ((second.id, third.id), third.id)

    QTest.mouseClick(
        strip._cards[third.id],
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert strip.selected_block_ids == (second.id,)
    assert strip._selected_id == second.id


def test_delete_button_emits_request_without_mutating_page(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    block = make_block(page.id, UUID(int=1), 1, "hello", x=5, y=5, width=20, height=20)
    page.blocks = [block]
    strip = BlockStrip()
    requested = []
    strip.delete_requested.connect(requested.append)
    strip.set_page(page)

    strip._cards[block.id].delete_button.click()

    assert requested == [block.id]
    assert page.blocks == [block]
    assert block.id in strip._cards


def test_set_page_none_clears_and_resets_status(qapp, tmp_path) -> None:
    path = tmp_path / "page.png"
    save_image(path)
    page = Page(source_path=str(path), width=100, height=80)
    block = make_block(page.id, UUID(int=1), 1, "hello", x=5, y=5, width=20, height=20)
    page.blocks = [block]
    strip = BlockStrip()
    strip.set_page(page)

    strip.set_selected(block.id)
    strip.set_page(None)

    assert strip._cards == {}
    assert strip._status_label.text() == "No page selected"
    assert not strip._status_label.isHidden()


def test_missing_and_invalid_images_show_error_without_crashing(qapp, tmp_path) -> None:
    page = Page(source_path=str(tmp_path / "missing.png"), width=100, height=80)
    strip = BlockStrip()
    strip.set_page(page)
    assert strip._status_label.text().startswith("Page image not found:")
    assert str(page.source_path) in strip._status_label.text()
    assert strip._cards == {}

    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")
    strip.set_page(page.model_copy(update={"source_path": str(invalid)}))
    assert strip._status_label.text().startswith("Could not read page image:")
    assert str(invalid) in strip._status_label.text()
    assert strip._cards == {}
