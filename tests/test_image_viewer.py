from uuid import uuid4

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from app.core.models import BoundingBox, Page, TextBlock
from app.ui.image_viewer import ImageViewer


def make_page(tmp_path, *, blocks=None, width=200, height=120) -> Page:
    path = tmp_path / "หน้า.png"
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(path))
    page_id = uuid4()
    return Page(
        id=page_id,
        source_path=str(path),
        width=width,
        height=height,
        blocks=blocks or [],
    )


def test_load_native_scene_and_failure(qapp, tmp_path):
    viewer = ImageViewer()
    page = make_page(tmp_path)

    viewer.set_page(page)
    assert viewer.sceneRect().size().toSize().width() == 200
    assert viewer.sceneRect().size().toSize().height() == 120

    viewer.set_page(None)
    assert viewer.scene().items() == []
    with pytest.raises(ValueError, match="does not exist"):
        viewer.set_page(page.model_copy(update={"source_path": str(tmp_path / "missing.png")}))

    invalid = tmp_path / "invalid.png"
    invalid.write_text("not an image")
    with pytest.raises(ValueError, match="Unable to read"):
        viewer.set_page(page.model_copy(update={"source_path": str(invalid)}))


def test_zoom_reset_and_fit(qapp, tmp_path):
    viewer = ImageViewer()
    viewer.resize(400, 300)
    viewer.set_page(make_page(tmp_path))

    viewer.zoom_in()
    assert viewer.transform().m11() > 1
    viewer.zoom_out()
    assert viewer.transform().m11() == pytest.approx(1)
    viewer.zoom_in()
    viewer.reset_zoom()
    assert viewer.transform().m11() == pytest.approx(1)
    viewer.fit_to_window()
    assert viewer.transform().m11() > 0


def test_create_select_update_and_delete(qapp, tmp_path):
    viewer = ImageViewer()
    page = make_page(tmp_path)
    created, selected, deleted = [], [], []
    viewer.block_created.connect(created.append)
    viewer.block_selected.connect(selected.append)
    viewer.block_deleted.connect(deleted.append)
    viewer.set_page(page)

    viewer._create_block(QPointF(20, 10), QPointF(80, 50))
    block = created[0]
    assert block.page_id == page.id
    assert block.reading_order == 1
    assert block.bbox == BoundingBox(x=20, y=10, width=60, height=40)
    assert viewer.selected_block_id == block.id
    assert selected[-1] == block.id

    changed = block.model_copy(update={"source_text": "更新", "reading_order": 4})
    emitted = []
    viewer.block_changed.connect(emitted.append)
    viewer.update_block(changed)
    assert viewer._items[block.id].block == changed
    assert viewer._items[block.id].label.text() == "4"
    assert emitted == []

    viewer.select_block(block.id)
    viewer.delete_selected_block()
    assert deleted == [block.id]
    assert viewer.selected_block_id is None


def test_draw_mode_maps_zoomed_view_back_to_image_pixels(qapp, tmp_path):
    viewer = ImageViewer()
    viewer.resize(120, 100)
    viewer.set_page(make_page(tmp_path))
    viewer.show()
    viewer.zoom_in()
    viewer.centerOn(100, 60)
    qapp.processEvents()
    created = []
    viewer.block_created.connect(created.append)
    viewer.set_draw_mode(True)

    start = QPointF(70, 30)
    end = QPointF(130, 80)
    QTest.mousePress(viewer.viewport(), Qt.MouseButton.LeftButton, pos=viewer.mapFromScene(start))
    QTest.mouseMove(viewer.viewport(), viewer.mapFromScene(end))
    QTest.mouseRelease(viewer.viewport(), Qt.MouseButton.LeftButton, pos=viewer.mapFromScene(end))

    assert len(created) == 1
    assert created[0].bbox.x == pytest.approx(start.x(), abs=1)
    assert created[0].bbox.y == pytest.approx(start.y(), abs=1)
    assert created[0].bbox.width == pytest.approx(end.x() - start.x(), abs=1)
    assert created[0].bbox.height == pytest.approx(end.y() - start.y(), abs=1)


def test_move_resize_clamp_and_identity_survive_view_transform(qapp, tmp_path):
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=40, height=30),
        reading_order=2,
    )
    page = make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id})
    viewer = ImageViewer()
    changed = []
    viewer.block_changed.connect(changed.append)
    viewer.set_page(page)
    item = viewer._items[block.id]

    viewer.zoom_in()
    viewer.horizontalScrollBar().setValue(7)
    viewer.verticalScrollBar().setValue(5)
    assert item.block.bbox == block.bbox
    item.setPos(999, 999)
    viewer._commit_item(item, block.bbox)
    moved = changed[-1]
    assert moved.id == block.id
    assert moved.page_id == block.page_id
    assert moved.bbox == BoundingBox(x=160, y=90, width=40, height=30)

    before_resize = moved.bbox
    item.setRect(0, 0, 100, 100)
    viewer._commit_item(item, before_resize)
    resized = changed[-1]
    assert resized.id == block.id
    assert resized.page_id == block.page_id
    assert resized.bbox.x == 160
    assert resized.bbox.y == 90
    assert resized.bbox.width == 40
    assert resized.bbox.height == 30
    assert viewer.transform().m11() > 1


def test_mouse_drag_moves_block_and_clamps_to_image(qapp, tmp_path):
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=40, height=30),
        reading_order=1,
    )
    viewer = ImageViewer()
    viewer.resize(320, 240)
    viewer.set_page(make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id}))
    viewer.show()
    viewer.select_block(block.id)
    qapp.processEvents()
    changed = []
    viewer.block_changed.connect(changed.append)

    start = viewer.mapFromScene(QPointF(30, 25))
    end = viewer.mapFromScene(QPointF(250, 150))
    QTest.mousePress(viewer.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewer.viewport(), end)
    QTest.mouseRelease(viewer.viewport(), Qt.MouseButton.LeftButton, pos=end)

    moved = changed[-1]
    assert moved.id == block.id
    assert moved.page_id == block.page_id
    assert moved.bbox == BoundingBox(x=160, y=90, width=40, height=30)


def test_mouse_drag_resizes_bottom_right_handle_and_clamps(qapp, tmp_path):
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=40, height=30),
        reading_order=1,
    )
    viewer = ImageViewer()
    viewer.resize(320, 240)
    viewer.set_page(make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id}))
    viewer.show()
    viewer.select_block(block.id)
    qapp.processEvents()
    changed = []
    viewer.block_changed.connect(changed.append)

    handle = viewer.mapFromScene(QPointF(47, 37))
    beyond_image = viewer.mapFromScene(QPointF(250, 150))
    QTest.mousePress(viewer.viewport(), Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(viewer.viewport(), beyond_image)
    QTest.mouseRelease(viewer.viewport(), Qt.MouseButton.LeftButton, pos=beyond_image)

    resized = changed[-1]
    assert resized.id == block.id
    assert resized.page_id == block.page_id
    assert resized.bbox == BoundingBox(x=10, y=10, width=190, height=110)


def test_delete_key_and_small_draw_are_safe(qapp, tmp_path):
    block = TextBlock(
        page_id=(page_id := uuid4()),
        bbox=BoundingBox(x=1, y=1, width=10, height=10),
        reading_order=0,
    )
    viewer = ImageViewer()
    viewer.set_page(make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id}))
    viewer.select_block(block.id)
    viewer._create_block(QPointF(2, 2), QPointF(3, 3))
    assert len(viewer._items) == 1

    from PySide6.QtGui import QKeyEvent

    viewer.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )
    assert viewer._items == {}
