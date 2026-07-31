"""Image viewer with editable text-block overlays in image-pixel coordinates."""

from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from app.core.coordinates import clamp_bbox, normalize_bbox
from app.core.models import BoundingBox, Page, TextBlock, utc_now

MIN_BLOCK_SIZE = 5.0
HANDLE_SIZE = 8.0
ZOOM_FACTOR = 1.2


class _BlockItem(QGraphicsRectItem):
    def __init__(self, viewer: "ImageViewer", block: TextBlock) -> None:
        super().__init__(0, 0, block.bbox.width, block.bbox.height)
        self.viewer = viewer
        self.block = block
        self.setPos(block.bbox.x, block.bbox.y)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPen(QPen(QColor("#e53935"), 2))
        self.setBrush(QColor(229, 57, 53, 28))
        self.label = QGraphicsSimpleTextItem(str(block.reading_order), self)
        self.label.setBrush(QColor("#e53935"))
        self.label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._resizing = False
        self._before_edit = block.bbox

    def set_block(self, block: TextBlock) -> None:
        self.block = block
        self.setPos(block.bbox.x, block.bbox.y)
        self.setRect(0, 0, block.bbox.width, block.bbox.height)
        self.label.setText(str(block.reading_order))

    def _handle_rect(self) -> QRectF:
        rect = self.rect()
        return QRectF(
            rect.right() - HANDLE_SIZE,
            rect.bottom() - HANDLE_SIZE,
            HANDLE_SIZE,
            HANDLE_SIZE,
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.fillRect(self._handle_rect(), QColor("#e53935"))

    def mousePressEvent(self, event) -> None:
        self._before_edit = self.block.bbox
        self._resizing = self._handle_rect().contains(event.pos())
        if self._resizing:
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self._resizing:
            super().mouseMoveEvent(event)
            return
        image = self.viewer.image_rect
        width = min(max(MIN_BLOCK_SIZE, event.pos().x()), image.width() - self.pos().x())
        height = min(max(MIN_BLOCK_SIZE, event.pos().y()), image.height() - self.pos().y())
        self.setRect(0, 0, width, height)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self.viewer._commit_item(self, self._before_edit)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            image = self.viewer.image_rect
            point = QPointF(value)
            return QPointF(
                min(max(0.0, point.x()), max(0.0, image.width() - self.rect().width())),
                min(max(0.0, point.y()), max(0.0, image.height() - self.rect().height())),
            )
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self.viewer._item_selected(self)
        return super().itemChange(change, value)


class ImageViewer(QGraphicsView):
    """Display a page without transforming its persisted image coordinates."""

    block_selected = Signal(object)
    block_created = Signal(object)
    block_changed = Signal(object)
    block_deleted = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._page: Page | None = None
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._items: dict[UUID, _BlockItem] = {}
        self._draw_mode = False
        self._draw_start: QPointF | None = None
        self._draw_preview: QGraphicsRectItem | None = None
        self._pan_start: QPoint | None = None

    @property
    def image_rect(self) -> QRectF:
        return self.sceneRect()

    @property
    def selected_block_id(self) -> UUID | None:
        selected = self._scene.selectedItems()
        return selected[0].block.id if selected and isinstance(selected[0], _BlockItem) else None

    def set_page(self, page: Page | None) -> None:
        self._clear_page()
        if page is None:
            return
        path = Path(page.source_path)
        if not path.is_file():
            raise ValueError(f"Image file does not exist: {path}")
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError(f"Unable to read image file: {path}")

        self._page = page
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(-1)
        self._scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        for block in page.blocks:
            self._add_block(block)

    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def fit_to_window(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def reset_zoom(self) -> None:
        self.resetTransform()

    def zoom_in(self) -> None:
        self.scale(ZOOM_FACTOR, ZOOM_FACTOR)

    def zoom_out(self) -> None:
        self.scale(1 / ZOOM_FACTOR, 1 / ZOOM_FACTOR)

    def select_block(self, block_id: UUID) -> None:
        item = self._items.get(block_id)
        if item is None:
            return
        self._scene.clearSelection()
        item.setSelected(True)
        self.ensureVisible(item)

    def update_block(self, block: TextBlock) -> None:
        item = self._items.get(block.id)
        if item is None or self._page is None or block.page_id != self._page.id:
            return
        item.set_block(block)

    def delete_selected_block(self) -> None:
        block_id = self.selected_block_id
        if block_id is None:
            return
        self._scene.removeItem(self._items.pop(block_id))
        self.block_deleted.emit(block_id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() > 0:
            self.zoom_in()
        elif event.angleDelta().y() < 0:
            self.zoom_out()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected_block()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._draw_mode and event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.image_rect.contains(point):
                self._draw_start = point
                self._draw_preview = self._scene.addRect(
                    QRectF(point, point), QPen(QColor("#1976d2"), 2)
                )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._draw_start is not None and self._draw_preview is not None:
            point = self.mapToScene(event.position().toPoint())
            self._draw_preview.setRect(QRectF(self._draw_start, point).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.setCursor(
                Qt.CursorShape.CrossCursor if self._draw_mode else Qt.CursorShape.ArrowCursor
            )
            event.accept()
            return
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._draw_start is not None
            and self._draw_preview is not None
        ):
            end = self.mapToScene(event.position().toPoint())
            self._scene.removeItem(self._draw_preview)
            self._draw_preview = None
            start, self._draw_start = self._draw_start, None
            self._create_block(start, end)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _clear_page(self) -> None:
        self._scene.clear()
        self._scene.setSceneRect(QRectF())
        self._page = None
        self._pixmap_item = None
        self._items.clear()
        self._draw_start = None
        self._draw_preview = None

    def _add_block(self, block: TextBlock) -> _BlockItem:
        item = _BlockItem(self, block)
        self._scene.addItem(item)
        self._items[block.id] = item
        return item

    def _create_block(self, start: QPointF, end: QPointF) -> None:
        if self._page is None:
            return
        if abs(end.x() - start.x()) < MIN_BLOCK_SIZE or abs(end.y() - start.y()) < MIN_BLOCK_SIZE:
            return
        bbox = clamp_bbox(
            normalize_bbox(start.x(), start.y(), end.x(), end.y()),
            self.image_rect.width(),
            self.image_rect.height(),
        )
        if bbox.width < MIN_BLOCK_SIZE or bbox.height < MIN_BLOCK_SIZE:
            return
        block = TextBlock(
            page_id=self._page.id,
            bbox=bbox,
            reading_order=max(
                (item.block.reading_order for item in self._items.values()), default=0
            )
            + 1,
        )
        self._scene.clearSelection()
        self._add_block(block).setSelected(True)
        self.block_created.emit(block)

    def _item_selected(self, item: _BlockItem) -> None:
        self.block_selected.emit(item.block.id)

    def _commit_item(self, item: _BlockItem, previous: BoundingBox) -> None:
        bbox = clamp_bbox(
            BoundingBox(
                x=item.pos().x(),
                y=item.pos().y(),
                width=item.rect().width(),
                height=item.rect().height(),
            ),
            self.image_rect.width(),
            self.image_rect.height(),
        )
        if bbox == previous:
            return
        item.block = item.block.model_copy(update={"bbox": bbox, "updated_at": utc_now()})
        item.set_block(item.block)
        self.block_changed.emit(item.block)
