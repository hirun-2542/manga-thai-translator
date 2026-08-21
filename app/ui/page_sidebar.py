"""Page navigation sidebar."""

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from app.core.models import Page


class PageSidebar(QListWidget):
    """List project pages while retaining their stable identifiers."""

    page_selected = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Page list")
        self.setToolTip("Select a page to view and edit its text blocks.")
        self.currentItemChanged.connect(self._emit_page_selected)

    def set_pages(self, pages: list[Page]) -> None:
        blocker = QSignalBlocker(self)
        self.clear()
        for number, page in enumerate(pages, start=1):
            filename = Path(page.source_path).name
            count = len(page.blocks)
            label = "block" if count == 1 else "blocks"
            item = QListWidgetItem(f"{number}. {filename} — {count} {label}")
            item.setData(Qt.ItemDataRole.UserRole, page.id)
            self.addItem(item)
        del blocker

    def select_page(self, page_id: UUID) -> None:
        for row in range(self.count()):
            if self.item(row).data(Qt.ItemDataRole.UserRole) == page_id:
                self.setCurrentRow(row)
                return

    def _emit_page_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is not None:
            self.page_selected.emit(current.data(Qt.ItemDataRole.UserRole))
