from uuid import uuid4

from PySide6.QtCore import Qt

from app.core.models import BoundingBox, Page, TextBlock
from app.ui.page_sidebar import PageSidebar


def test_page_labels_and_uuid_selection(qapp) -> None:
    first = Page(source_path="ภาพ/หน้า一.png", width=100, height=200)
    second = Page(source_path="ตอนที่ ๑/หน้าสอง.webp", width=100, height=200)
    second.blocks.append(
        TextBlock(
            page_id=second.id,
            bbox=BoundingBox(x=1, y=2, width=3, height=4),
            reading_order=0,
        )
    )
    sidebar = PageSidebar()
    selected = []
    sidebar.page_selected.connect(selected.append)

    sidebar.set_pages([first, second])

    assert sidebar.item(0).text() == "1. หน้า一.png — 0 blocks"
    assert sidebar.item(1).text() == "2. หน้าสอง.webp — 1 blocks"
    assert sidebar.item(1).data(Qt.ItemDataRole.UserRole) == second.id

    sidebar.select_page(second.id)
    assert sidebar.currentRow() == 1
    assert selected == [second.id]

    sidebar.select_page(uuid4())
    assert sidebar.currentRow() == 1
