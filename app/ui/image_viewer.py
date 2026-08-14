"""Image viewer with editable text-block overlays in image-pixel coordinates."""

from collections.abc import Iterable
from math import atan2, degrees
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from app.core.coordinates import clamp_bbox, normalize_bbox, rotated_bbox_bounds
from app.core.models import BoundingBox, Page, TextBlock, utc_now

MIN_BLOCK_SIZE = 5.0
HANDLE_SIZE = 8.0
ROTATION_HANDLE_OFFSET = 22.0
ZOOM_FACTOR = 1.2
MIN_MANUAL_ZOOM = 0.1
MAX_MANUAL_ZOOM = 8.0


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
        self.setAcceptHoverEvents(True)
        self.label = QGraphicsSimpleTextItem(str(block.reading_order), self)
        self.label.setBrush(QColor("#e53935"))
        self.label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._resizing = False
        self._rotating = False
        self._before_edit = block
        self.set_block(block)

    def set_block(self, block: TextBlock) -> None:
        self.block = block
        self.setPos(block.bbox.x, block.bbox.y)
        self.setRect(0, 0, block.bbox.width, block.bbox.height)
        self.setTransformOriginPoint(self.rect().center())
        self.setRotation(block.rotation_degrees)
        self.label.setText(str(block.reading_order))

    def _handle_rect(self) -> QRectF:
        rect = self.rect()
        return QRectF(
            rect.right() - HANDLE_SIZE,
            rect.bottom() - HANDLE_SIZE,
            HANDLE_SIZE,
            HANDLE_SIZE,
        )

    def _rotation_handle_rect(self) -> QRectF:
        center = self.rect().center()
        return QRectF(
            center.x() - HANDLE_SIZE / 2,
            self.rect().top() - ROTATION_HANDLE_OFFSET - HANDLE_SIZE / 2,
            HANDLE_SIZE,
            HANDLE_SIZE,
        )

    def boundingRect(self) -> QRectF:
        return super().boundingRect().united(self._rotation_handle_rect().adjusted(-2, -2, 2, 2))

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.rect())
        path.addEllipse(self._rotation_handle_rect())
        return path

    def paint(self, painter: QPainter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.fillRect(self._handle_rect(), QColor("#e53935"))
            center = self.rect().center()
            handle = self._rotation_handle_rect()
            painter.drawLine(center.x(), self.rect().top(), center.x(), handle.center().y())
            painter.setBrush(QColor("#e53935"))
            painter.drawEllipse(handle)

    def mousePressEvent(self, event) -> None:
        self._before_edit = self.block
        self._rotating = self._rotation_handle_rect().contains(event.pos())
        if self._rotating:
            self.setSelected(True)
            event.accept()
            return
        self._resizing = self._handle_rect().contains(event.pos())
        if self._resizing:
            self.setSelected(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rotating:
            center = self.mapToScene(self.rect().center())
            delta = event.scenePos() - center
            angle = degrees(atan2(delta.y(), delta.x())) + 90.0
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                angle = round(angle / 15.0) * 15.0
            self.setRotation(_normalized_angle(angle))
            event.accept()
            return
        if not self._resizing:
            super().mouseMoveEvent(event)
            return
        image = self.viewer.image_rect
        width = min(max(MIN_BLOCK_SIZE, event.pos().x()), image.width() - self.pos().x())
        height = min(max(MIN_BLOCK_SIZE, event.pos().y()), image.height() - self.pos().y())
        self.setRect(0, 0, width, height)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._rotating:
            self._rotating = False
            event.accept()
        elif self._resizing:
            self._resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)
        self.viewer._commit_item(self, self._before_edit)

    def hoverMoveEvent(self, event) -> None:
        if self._rotation_handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._handle_rect().contains(event.pos()):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            image = self.viewer.image_rect
            point = QPointF(value)
            bounds = rotated_bbox_bounds(
                BoundingBox(
                    x=point.x(),
                    y=point.y(),
                    width=self.rect().width(),
                    height=self.rect().height(),
                ),
                self.rotation(),
            )
            shift_x = (
                -bounds.x if bounds.x < 0 else min(0.0, image.width() - bounds.x - bounds.width)
            )
            shift_y = (
                -bounds.y if bounds.y < 0 else min(0.0, image.height() - bounds.y - bounds.height)
            )
            return QPointF(point.x() + shift_x, point.y() + shift_y)
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(value):
            self.viewer._item_selected(self)
        return super().itemChange(change, value)


class ImageViewer(QGraphicsView):
    """Display a page without transforming its persisted image coordinates."""

    block_selected = Signal(object)
    block_created = Signal(object)
    block_changed = Signal(object)
    block_deleted = Signal(object)
    blocks_deleted = Signal(object)
    selection_changed = Signal(object)
    view_transform_changed = Signal()

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
        self._primary_block_id: UUID | None = None
        self._selection_history: list[UUID] = []
        self._selection_blocked = 0
        self._pressed_item: _BlockItem | None = None
        self._pressed_was_selected = False
        self._pressed_additive = False
        self._selection_pending = False
        self._last_emitted_selection: tuple[tuple[UUID, ...], UUID | None] = ((), None)
        self._scene.selectionChanged.connect(self._scene_selection_changed)

    @property
    def image_rect(self) -> QRectF:
        return self.sceneRect()

    @property
    def selected_block_id(self) -> UUID | None:
        if self._primary_block_id in self.selected_block_ids:
            return self._primary_block_id
        return self.selected_block_ids[-1] if self.selected_block_ids else None

    @property
    def selected_block_ids(self) -> tuple[UUID, ...]:
        """Return selected IDs in the current page's stable reading order."""
        selected = {
            item.block.id for item in self._scene.selectedItems() if isinstance(item, _BlockItem)
        }
        return tuple(
            block.id
            for block in sorted(
                (item.block for item in self._items.values()),
                key=lambda item: (item.reading_order, str(item.id)),
            )
            if block.id in selected
        )

    @property
    def zoom_percent(self) -> float:
        return self.transform().m11() * 100.0

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

    def set_display_image(self, path: str | Path) -> None:
        """Swap the displayed pixels while preserving the page and its overlays."""
        if self._page is None or self._pixmap_item is None:
            raise ValueError("No page is loaded")
        image_path = Path(path)
        if not image_path.is_file():
            raise ValueError(f"Image file does not exist: {image_path}")
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise ValueError(f"Unable to read image file: {image_path}")
        if pixmap.size() != self._pixmap_item.pixmap().size():
            raise ValueError("Display image dimensions must match the loaded page")
        self._pixmap_item.setPixmap(pixmap)

    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def fit_to_window(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self.view_transform_changed.emit()

    def reset_zoom(self) -> None:
        self.resetTransform()
        self.view_transform_changed.emit()

    def zoom_in(self) -> None:
        self._set_manual_zoom(self.transform().m11() * ZOOM_FACTOR)

    def zoom_out(self) -> None:
        self._set_manual_zoom(self.transform().m11() / ZOOM_FACTOR)

    def select_block(self, block_id: UUID) -> None:
        if block_id not in self._items:
            return
        self.set_selected_block_ids((block_id,), primary_id=block_id)
        self.ensureVisible(self._items[block_id])

    def set_selected_block_ids(
        self,
        block_ids: Iterable[UUID],
        *,
        primary_id: UUID | None = None,
    ) -> None:
        """Replace the current selection without emitting intermediate states."""
        selected = {block_id for block_id in block_ids if block_id in self._items}
        ordered = (
            tuple(
                block.id
                for block in sorted(
                    (item.block for item in self._items.values()),
                    key=lambda item: (item.reading_order, str(item.id)),
                )
                if block.id in selected
            )
            if self._page is not None
            else ()
        )
        primary = primary_id if primary_id in selected else (ordered[-1] if ordered else None)
        self._selection_blocked += 1
        try:
            self._scene.clearSelection()
            for block_id in ordered:
                self._items[block_id].setSelected(True)
        finally:
            self._selection_blocked -= 1
        self._selection_history = list(ordered)
        self._primary_block_id = primary
        self._emit_selection_changed()

    def clear_selection(self) -> None:
        self.set_selected_block_ids(())

    def update_block(self, block: TextBlock) -> None:
        item = self._items.get(block.id)
        if item is None or self._page is None or block.page_id != self._page.id:
            return
        item.set_block(block)

    def fit_block_to_image(self, block: TextBlock) -> TextBlock:
        """Keep an edited block's visible bounds inside the loaded image."""
        if self._page is None or block.page_id != self._page.id:
            return block
        bbox = (
            clamp_bbox(block.bbox, self.image_rect.width(), self.image_rect.height())
            if block.rotation_degrees == 0.0
            else self._fit_rotated_region(block.bbox, block.rotation_degrees)
        )
        return block if bbox == block.bbox else block.model_copy(update={"bbox": bbox})

    def delete_selected_block(self) -> None:
        self.delete_block_ids(self.selected_block_ids)

    def delete_block_ids(self, block_ids: Iterable[UUID]) -> None:
        requested = set(block_ids)
        ids = tuple(block_id for block_id in requested if block_id in self._items)
        if not ids:
            return
        ids = tuple(
            block_id for block_id in self.selected_block_ids if block_id in requested
        ) + tuple(
            block_id
            for block_id in self._items
            if block_id in requested and block_id not in self.selected_block_ids
        )
        self._selection_blocked += 1
        try:
            for block_id in ids:
                self._scene.removeItem(self._items.pop(block_id))
        finally:
            self._selection_blocked -= 1
        self._selection_history = [
            block_id for block_id in self._selection_history if block_id not in ids
        ]
        if self._primary_block_id in ids:
            self._primary_block_id = (
                self._selection_history[-1] if self._selection_history else None
            )
        self._emit_selection_changed()
        self.blocks_deleted.emit(ids)
        for block_id in ids:
            self.block_deleted.emit(block_id)

    def wheelEvent(self, event: QWheelEvent) -> None:
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.pixelDelta().y() or event.angleDelta().y()
            if delta:
                self._set_manual_zoom(
                    self.transform().m11() * ZOOM_FACTOR ** (delta / 120.0),
                    event.position().toPoint(),
                )
            event.accept()
            return

        horizontal = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if horizontal:
            pixel_delta = event.pixelDelta().x() or event.pixelDelta().y()
            angle_delta = event.angleDelta().x() or event.angleDelta().y()
        else:
            pixel_delta = event.pixelDelta().y()
            angle_delta = event.angleDelta().y()
        scrollbar = self.horizontalScrollBar() if horizontal else self.verticalScrollBar()
        distance = pixel_delta or round(angle_delta / 120.0 * scrollbar.singleStep() * 3)
        if distance:
            scrollbar.setValue(scrollbar.value() - distance)
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
        if event.button() == Qt.MouseButton.LeftButton:
            candidate = self.itemAt(event.position().toPoint())
            while candidate is not None and not isinstance(candidate, _BlockItem):
                candidate = candidate.parentItem()
            self._pressed_item = candidate if isinstance(candidate, _BlockItem) else None
            self._pressed_was_selected = bool(
                self._pressed_item is not None and self._pressed_item.isSelected()
            )
            self._pressed_additive = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._selection_pending = True
            if self._pressed_item is None and not self._pressed_additive:
                self._scene.clearSelection()
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
        if event.button() == Qt.MouseButton.LeftButton and self._selection_pending:
            self._finish_mouse_selection()

    def _clear_page(self) -> None:
        had_selection = bool(self.selected_block_ids)
        self._selection_blocked += 1
        try:
            self._pixmap_item = None
            self._items.clear()
            self._draw_preview = None
            self._pressed_item = None
            self._scene.clear()
        finally:
            self._selection_blocked -= 1
        self._scene.setSceneRect(QRectF())
        self._page = None
        self._primary_block_id = None
        self._selection_history.clear()
        self._draw_start = None
        self._selection_pending = False
        if had_selection:
            self._emit_selection_changed()

    def _set_manual_zoom(self, requested_scale: float, anchor: QPoint | None = None) -> None:
        target_scale = min(MAX_MANUAL_ZOOM, max(MIN_MANUAL_ZOOM, requested_scale))
        current_scale = self.transform().m11()
        if current_scale <= 0 or target_scale == current_scale:
            return

        anchor = anchor or self.viewport().rect().center()
        scene_anchor = self.mapToScene(anchor)
        self.scale(target_scale / current_scale, target_scale / current_scale)
        shifted_anchor = self.mapToScene(anchor)
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center + scene_anchor - shifted_anchor)
        self.view_transform_changed.emit()

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
        self._add_block(block)
        self.set_selected_block_ids((block.id,), primary_id=block.id)
        self.block_created.emit(block)

    def _scene_selection_changed(self) -> None:
        if self._selection_blocked or self._selection_pending:
            return
        self._emit_selection_changed()

    def _item_selected(self, item: _BlockItem) -> None:
        del item

    def _finish_mouse_selection(self) -> None:
        item = self._pressed_item
        additive = self._pressed_additive
        was_selected = self._pressed_was_selected
        self._selection_pending = False
        self._pressed_item = None
        if item is None:
            if not self.selected_block_ids:
                self._selection_history.clear()
                self._primary_block_id = None
            self._emit_selection_changed()
            return
        if not additive:
            self.set_selected_block_ids((item.block.id,), primary_id=item.block.id)
            return
        desired_selected = not was_selected
        if item.isSelected() != desired_selected:
            self._selection_blocked += 1
            try:
                item.setSelected(desired_selected)
            finally:
                self._selection_blocked -= 1
        if desired_selected:
            self._selection_history = [
                block_id for block_id in self._selection_history if block_id != item.block.id
            ]
            self._selection_history.append(item.block.id)
            self._primary_block_id = item.block.id
        else:
            self._selection_history = [
                block_id for block_id in self._selection_history if block_id != item.block.id
            ]
            if self._primary_block_id == item.block.id:
                self._primary_block_id = (
                    self._selection_history[-1] if self._selection_history else None
                )
        self._emit_selection_changed()

    def _emit_selection_changed(self) -> None:
        selected_ids = self.selected_block_ids
        selected_set = set(selected_ids)
        self._selection_history = [
            block_id for block_id in self._selection_history if block_id in selected_set
        ]
        for block_id in selected_ids:
            if block_id not in self._selection_history:
                self._selection_history.append(block_id)
        if self._primary_block_id not in selected_set:
            self._primary_block_id = (
                self._selection_history[-1] if self._selection_history else None
            )
        state = (selected_ids, self._primary_block_id)
        if state == self._last_emitted_selection:
            return
        self._last_emitted_selection = state
        self.selection_changed.emit(state)
        self.block_selected.emit(self._primary_block_id)

    def _commit_item(self, item: _BlockItem, previous: TextBlock) -> None:
        candidate = BoundingBox(
            x=item.pos().x(),
            y=item.pos().y(),
            width=item.rect().width(),
            height=item.rect().height(),
        )
        rotation = _normalized_angle(item.rotation())
        bbox = (
            clamp_bbox(candidate, self.image_rect.width(), self.image_rect.height())
            if rotation == 0.0
            else self._fit_rotated_region(candidate, rotation)
        )
        if bbox == previous.bbox and rotation == previous.rotation_degrees:
            return
        item.block = item.block.model_copy(
            update={
                "bbox": bbox,
                "rotation_degrees": rotation,
                "updated_at": utc_now(),
            }
        )
        item.set_block(item.block)
        self.block_changed.emit(item.block)

    def _fit_rotated_region(self, bbox: BoundingBox, rotation_degrees: float) -> BoundingBox:
        image_width = self.image_rect.width()
        image_height = self.image_rect.height()
        bounds = rotated_bbox_bounds(bbox, rotation_degrees)
        scale = min(1.0, image_width / bounds.width, image_height / bounds.height)
        if scale < 1.0:
            bbox = bbox.model_copy(
                update={
                    "width": max(MIN_BLOCK_SIZE, bbox.width * scale),
                    "height": max(MIN_BLOCK_SIZE, bbox.height * scale),
                }
            )
            bounds = rotated_bbox_bounds(bbox, rotation_degrees)
        shift_x = -bounds.x if bounds.x < 0 else min(0.0, image_width - bounds.x - bounds.width)
        shift_y = -bounds.y if bounds.y < 0 else min(0.0, image_height - bounds.y - bounds.height)
        return clamp_bbox(
            bbox.model_copy(update={"x": bbox.x + shift_x, "y": bbox.y + shift_y}),
            image_width,
            image_height,
        )


def _normalized_angle(value: float) -> float:
    normalized = (value + 180.0) % 360.0 - 180.0
    return 180.0 if normalized == -180.0 and value > 0 else normalized
