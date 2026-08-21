from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QWheelEvent
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


def test_page_transition_drops_scene_item_refs_before_clear(qapp, tmp_path) -> None:
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=40, height=30),
        reading_order=1,
    )
    first_page = make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id})
    second_page = make_page(tmp_path).model_copy(update={"id": uuid4()})
    viewer = ImageViewer()
    viewer.set_page(first_page)
    scene = viewer._scene
    viewer._draw_preview = scene.addRect(QRectF(0, 0, 1, 1))
    viewer._pressed_item = viewer._items[block.id]

    class SceneProxy:
        def __getattr__(self, name):
            return getattr(scene, name)

        def clear(self):
            assert viewer._pixmap_item is None
            assert viewer._items == {}
            assert viewer._draw_preview is None
            assert viewer._pressed_item is None
            return scene.clear()

    viewer._scene = SceneProxy()
    viewer.set_page(second_page)


def test_zoom_reset_and_fit(qapp, tmp_path):
    viewer = ImageViewer()
    viewer.resize(400, 300)
    viewer.set_page(make_page(tmp_path))
    transforms = []
    viewer.view_transform_changed.connect(lambda: transforms.append(viewer.transform().m11()))

    viewer.zoom_in()
    assert viewer.transform().m11() > 1
    viewer.zoom_out()
    assert viewer.transform().m11() == pytest.approx(1)
    viewer.zoom_in()
    viewer.reset_zoom()
    assert viewer.transform().m11() == pytest.approx(1)
    viewer.fit_to_window()
    assert viewer.transform().m11() > 0
    assert len(transforms) == 5


def wheel_event(
    position: QPointF,
    delta: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    pixel_delta_y: int = 0,
) -> QWheelEvent:
    return QWheelEvent(
        position,
        position,
        QPoint(0, pixel_delta_y),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_wheel_routes_scroll_and_ctrl_zoom_without_cross_effects(qapp, tmp_path) -> None:
    viewer = ImageViewer()
    viewer.resize(240, 180)
    viewer.set_page(make_page(tmp_path, width=1000, height=1000))
    viewer.show()
    qapp.processEvents()
    viewer.horizontalScrollBar().setValue(300)
    viewer.verticalScrollBar().setValue(300)
    initial_scale = viewer.transform().m11()

    viewer.wheelEvent(wheel_event(QPointF(80, 70), -120))

    assert viewer.verticalScrollBar().value() > 300
    assert viewer.horizontalScrollBar().value() == 300
    assert viewer.transform().m11() == initial_scale

    viewer.wheelEvent(wheel_event(QPointF(80, 70), -120, Qt.KeyboardModifier.ShiftModifier))

    assert viewer.horizontalScrollBar().value() > 300
    assert viewer.transform().m11() == initial_scale

    horizontal_before_pixel = viewer.horizontalScrollBar().value()
    viewer.wheelEvent(
        wheel_event(
            QPointF(80, 70),
            0,
            Qt.KeyboardModifier.ShiftModifier,
            pixel_delta_y=-20,
        )
    )

    assert viewer.horizontalScrollBar().value() > horizontal_before_pixel
    assert viewer.transform().m11() == initial_scale

    before = viewer.mapToScene(QPoint(80, 70))
    viewer.wheelEvent(wheel_event(QPointF(80, 70), 120, Qt.KeyboardModifier.ControlModifier))
    after = viewer.mapToScene(QPoint(80, 70))

    assert viewer.transform().m11() > initial_scale
    assert after.x() == pytest.approx(before.x(), abs=1)
    assert after.y() == pytest.approx(before.y(), abs=1)


def test_manual_zoom_clamps_but_fit_can_go_below_minimum(qapp, tmp_path) -> None:
    viewer = ImageViewer()
    viewer.resize(400, 300)
    viewer.set_page(make_page(tmp_path, width=100, height=10000))
    viewer.show()
    qapp.processEvents()

    for _ in range(80):
        viewer.zoom_out()
    assert viewer.zoom_percent == pytest.approx(10)

    for _ in range(80):
        viewer.zoom_in()
    assert viewer.zoom_percent == pytest.approx(800)

    viewer.fit_to_window()
    assert viewer.zoom_percent < 10


def test_display_image_swap_preserves_page_overlays_and_selection(qapp, tmp_path):
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=40, height=30),
        reading_order=1,
    )
    viewer = ImageViewer()
    viewer.set_page(make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id}))
    viewer.select_block(block.id)
    item = viewer._items[block.id]
    replacement = tmp_path / "preview.png"
    image = QImage(200, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    assert image.save(str(replacement))

    viewer.set_display_image(replacement)

    assert viewer.selected_block_id == block.id
    assert viewer._items[block.id] is item
    assert viewer._page.id == page_id
    assert viewer._pixmap_item.pixmap().toImage().pixelColor(0, 0) == QColor("blue")

    wrong_size = tmp_path / "wrong.png"
    assert QImage(10, 10, QImage.Format.Format_RGB32).save(str(wrong_size))
    with pytest.raises(ValueError, match="dimensions"):
        viewer.set_display_image(wrong_size)


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


def test_ctrl_click_selects_subset_in_reading_order_and_page_reset(qapp, tmp_path) -> None:
    page_id = uuid4()
    first = TextBlock(
        id=UUID(int=10),
        page_id=page_id,
        bbox=BoundingBox(x=80, y=10, width=30, height=30),
        reading_order=1,
    )
    second = TextBlock(
        id=UUID(int=20),
        page_id=page_id,
        bbox=BoundingBox(x=10, y=10, width=30, height=30),
        reading_order=2,
    )
    third = TextBlock(
        id=UUID(int=30),
        page_id=page_id,
        bbox=BoundingBox(x=150, y=10, width=30, height=30),
        reading_order=3,
    )
    page = make_page(tmp_path, blocks=[third, second, first]).model_copy(update={"id": page_id})
    viewer = ImageViewer()
    states = []
    viewer.selection_changed.connect(states.append)
    viewer.resize(320, 240)
    viewer.set_page(page)
    viewer.show()
    qapp.processEvents()

    QTest.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        viewer.mapFromScene(QPointF(165, 25)),
    )
    QTest.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        viewer.mapFromScene(QPointF(95, 25)),
    )

    assert viewer.selected_block_ids == (first.id, third.id)
    assert viewer.selected_block_id == first.id

    QTest.mouseClick(
        viewer.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        viewer.mapFromScene(QPointF(165, 25)),
    )
    assert viewer.selected_block_ids == (first.id,)
    assert viewer.selected_block_id == first.id

    second_page = make_page(tmp_path, width=240, height=120)
    viewer.set_page(second_page)
    assert viewer.selected_block_ids == ()
    assert viewer.selected_block_id is None
    assert states[-1] == ((), None)


def test_delete_selected_blocks_removes_exact_set(qapp, tmp_path) -> None:
    page_id = uuid4()
    blocks = [
        TextBlock(
            id=UUID(int=index),
            page_id=page_id,
            bbox=BoundingBox(x=10 + index * 40, y=10, width=20, height=20),
            reading_order=index,
        )
        for index in (1, 2, 3)
    ]
    page = make_page(tmp_path, blocks=blocks).model_copy(update={"id": page_id})
    viewer = ImageViewer()
    viewer.set_page(page)
    deleted = []
    viewer.block_deleted.connect(deleted.append)
    viewer.set_selected_block_ids((blocks[0].id, blocks[2].id), primary_id=blocks[2].id)

    viewer.delete_selected_block()

    assert deleted == [blocks[0].id, blocks[2].id]
    assert tuple(viewer._items) == (blocks[1].id,)
    assert viewer.selected_block_ids == ()


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
    viewer._commit_item(item, block)
    moved = changed[-1]
    assert moved.id == block.id
    assert moved.page_id == block.page_id
    assert moved.bbox == BoundingBox(x=160, y=90, width=40, height=30)

    item.setRect(0, 0, 100, 100)
    viewer._commit_item(item, moved)
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


def test_rotation_handle_rotates_whole_region_around_its_center(qapp, tmp_path) -> None:
    page_id = uuid4()
    block = TextBlock(
        page_id=page_id,
        bbox=BoundingBox(x=60, y=40, width=60, height=30),
        reading_order=1,
    )
    viewer = ImageViewer()
    viewer.resize(320, 240)
    viewer.set_page(make_page(tmp_path, blocks=[block]).model_copy(update={"id": page_id}))
    viewer.show()
    viewer.select_block(block.id)
    qapp.processEvents()
    item = viewer._items[block.id]
    changed = []
    viewer.block_changed.connect(changed.append)
    center = item.mapToScene(item.rect().center())
    start = viewer.mapFromScene(item.mapToScene(item._rotation_handle_rect().center()))
    end = viewer.mapFromScene(center + QPointF(40, 0))

    QTest.mousePress(viewer.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(viewer.viewport(), end)
    QTest.mouseRelease(viewer.viewport(), Qt.MouseButton.LeftButton, pos=end)

    assert changed[-1].rotation_degrees == pytest.approx(90, abs=2)
    assert item.transformOriginPoint() == item.rect().center()


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
