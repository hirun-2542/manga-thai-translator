"""Compact current-page block navigation."""

from __future__ import annotations

from math import ceil, floor
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Page, TextBlock

_THUMBNAIL_SIZE = QSize(72, 56)

_STATUS_PRESENTATION = {
    "detected": ("•", "Detected", "normal"),
    "language_review_required": ("⚠", "Language review required", "warning"),
    "ocr_complete": ("•", "OCR complete", "success"),
    "ocr_reviewed": ("✓", "OCR reviewed", "success"),
    "translated": ("•", "Translated", "success"),
    "translation_reviewed": ("✓", "Translation reviewed", "success"),
    "error": ("⚠", "Error", "danger"),
}


class _BlockCard(QFrame):
    clicked = Signal()
    delete_requested = Signal(object)

    def __init__(self, block: TextBlock, thumbnail: QPixmap | None, parent=None) -> None:
        super().__init__(parent)
        self.block_id = block.id
        self.click_modifiers = Qt.KeyboardModifier.NoModifier
        self.setObjectName("ocrBlockCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(72)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"OCR block {block.reading_order}: {block.source_text}")
        self.setToolTip("Select this OCR block.")

        self.order_label = QLabel(f"#{block.reading_order}")
        self.order_label.setObjectName("readingOrderLabel")
        self.order_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setObjectName("blockThumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setFixedSize(_THUMBNAIL_SIZE)
        self.thumbnail_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.thumbnail_label.setAccessibleName(f"Thumbnail for OCR block {block.reading_order}")
        if thumbnail is None or thumbnail.isNull():
            self.thumbnail_label.setText("No thumbnail")
        else:
            self.thumbnail_label.setPixmap(thumbnail)

        self.source_label = QLabel(block.source_text)
        self.source_label.setObjectName("sourceOcrText")
        self.source_label.setWordWrap(True)
        self.source_label.setMaximumHeight(36)
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_label.setAccessibleName("OCR source text")
        self.source_label.setToolTip("Source text recognized by OCR for this block.")
        self.source_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        icon, status_text, status_tone = _STATUS_PRESENTATION[block.status.value]
        self.status_label = QLabel(f"{icon} {status_text}")
        self.status_label.setObjectName("blockStatusLabel")
        self.status_label.setProperty("statusTone", status_tone)
        self.status_label.setAccessibleName(f"Block status: {status_text}")

        self.delete_button = QPushButton("Delete")
        self.delete_button.setMinimumSize(36, 36)
        self.delete_button.setAccessibleName(f"Delete OCR block {block.reading_order}")
        self.delete_button.setToolTip("Delete this OCR block.")
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.block_id))

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(self.order_label)
        text_layout.addWidget(self.status_label)
        text_layout.addWidget(self.source_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.thumbnail_label)
        layout.addLayout(text_layout, 1)
        layout.addWidget(self.delete_button, alignment=Qt.AlignmentFlag.AlignTop)

        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setAccessibleDescription("OCR block selected" if selected else "OCR block")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_modifiers = event.modifiers()
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.click_modifiers = Qt.KeyboardModifier.NoModifier
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class BlockStrip(QWidget):
    """Show current-page blocks without owning or mutating the page model."""

    block_selected = Signal(object)
    selection_changed = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: dict[UUID, _BlockCard] = {}
        self._selected_id: UUID | None = None
        self._selected_ids: tuple[UUID, ...] = ()
        self._selection_history: list[UUID] = []
        self.setAccessibleName("Blocks on the current page")

        self._status_label = QLabel()
        self._status_label.setAccessibleName("OCR block list status")
        self._status_label.setWordWrap(True)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll_area.setAccessibleName("OCR block list")

        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._cards_layout = QVBoxLayout(self._content)
        self._cards_layout.setContentsMargins(6, 6, 6, 6)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        self._scroll_area.setWidget(self._content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._status_label)
        layout.addWidget(self._scroll_area)
        self._show_status("No page selected")

    def set_page(self, page: Page | None) -> None:
        """Load cards for ``page`` using its source path exactly as supplied."""
        self._clear_cards()
        self._selected_id = None
        self._selected_ids = ()
        self._selection_history.clear()
        if page is None:
            self._show_status("No page selected")
            return

        image, error = self._load_image(page.source_path)
        if error is not None:
            self._show_status(error)
            return

        blocks = sorted(page.blocks, key=lambda block: (block.reading_order, str(block.id)))
        if not blocks:
            self._show_status("This page has no text blocks")
            return

        for block in blocks:
            card = _BlockCard(block, self._thumbnail(image, block), self._content)
            card.clicked.connect(
                lambda card=card: self._select(card.block_id, card.click_modifiers)
            )
            card.delete_requested.connect(self.delete_requested.emit)
            self._cards[block.id] = card
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
        self._status_label.hide()
        self._scroll_area.show()

    def set_selected(self, block_id: UUID | None) -> None:
        """Highlight the card with ``block_id`` without emitting or changing the model."""
        self.set_selected_block_ids(
            (block_id,) if block_id is not None else (), primary_id=block_id
        )

    def set_selected_block_ids(
        self,
        block_ids: tuple[UUID, ...] | list[UUID] | set[UUID],
        *,
        primary_id: UUID | None = None,
    ) -> None:
        selected = set(block_ids) & self._cards.keys()
        self._selected_ids = tuple(block_id for block_id in self._cards if block_id in selected)
        self._selection_history = [
            block_id for block_id in self._selection_history if block_id in selected
        ]
        for block_id in self._selected_ids:
            if block_id not in self._selection_history:
                self._selection_history.append(block_id)
        if primary_id in selected:
            self._selection_history.remove(primary_id)
            self._selection_history.append(primary_id)
        self._selected_id = self._selection_history[-1] if self._selection_history else None
        for card_id, card in self._cards.items():
            card.set_selected(card_id in selected)

    @property
    def selected_block_ids(self) -> tuple[UUID, ...]:
        return self._selected_ids

    def _select(self, block_id: UUID, modifiers: Qt.KeyboardModifier) -> None:
        if block_id not in self._cards:
            return
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selected = set(self._selected_ids)
            if block_id in selected:
                selected.remove(block_id)
                primary_id = None
            else:
                selected.add(block_id)
                primary_id = block_id
        else:
            selected = {block_id}
            primary_id = block_id
        ordered = tuple(card_id for card_id in self._cards if card_id in selected)
        self.set_selected_block_ids(ordered, primary_id=primary_id)
        self.selection_changed.emit((self._selected_ids, self._selected_id))
        self.block_selected.emit(self._selected_id)

    def _clear_cards(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._cards.clear()
        self._cards_layout.addStretch()

    def _show_status(self, message: str) -> None:
        self._status_label.setText(message)
        self._status_label.show()
        self._scroll_area.hide()

    @staticmethod
    def _load_image(source_path: str) -> tuple[QImage | None, str | None]:
        path = Path(source_path)
        if not path.is_file():
            return None, f"Page image not found: {path}"
        image = QImage(str(path))
        if image.isNull():
            return None, f"Could not read page image: {path}"
        return image, None

    @staticmethod
    def _thumbnail(image: QImage | None, block: TextBlock) -> QPixmap | None:
        if image is None:
            return None
        left = max(0, min(image.width(), floor(block.bbox.x)))
        top = max(0, min(image.height(), floor(block.bbox.y)))
        right = max(0, min(image.width(), ceil(block.bbox.x + block.bbox.width)))
        bottom = max(0, min(image.height(), ceil(block.bbox.y + block.bbox.height)))
        if right <= left or bottom <= top:
            return None
        cropped = image.copy(QRect(left, top, right - left, bottom - top))
        if cropped.isNull():
            return None
        return QPixmap.fromImage(cropped).scaled(
            _THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
