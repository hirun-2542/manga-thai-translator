import asyncio
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import ImageFont
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QImage, QInputMethodEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QSplitter, QTabWidget

from app.core.coordinates import rotated_bbox_bounds
from app.core.models import (
    BlockStatus,
    BoundingBox,
    Page,
    Project,
    ProviderConfiguration,
    ReadingOrderPreset,
    SourceLanguage,
    TextBlock,
    TranslationResult,
)
from app.main import main
from app.persistence.project_repository import ProjectRepository
from app.services.export import (
    ExportIssue,
    ExportResult,
    ExportService,
    OverflowWarning,
    RenderMetric,
)
from app.services.iopaint_cleanup import IOPaintConfiguration
from app.services.text_detection import MockTextDetectionProvider
from app.services.workflow import WorkflowIssue, WorkflowResult
from app.ui.main_window import MainWindow
from app.ui.theme import COLORS, LIGHT_STUDIO_STYLESHEET
from app.ui.workers import (
    CleanupIssue,
    ImageCleanupWorker,
    PageCleanupResult,
    PageImageCleanupWorker,
)


@pytest.fixture(autouse=True)
def clean_up_top_level_widgets(qapp):
    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        "Manga Thai Translator",
        "Manga Thai Translator",
    )

    def clean_up() -> None:
        for widget in qapp.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        settings.clear()
        settings.sync()

    clean_up()
    yield
    clean_up()


def save_image(path: Path, color: Qt.GlobalColor = Qt.GlobalColor.white) -> None:
    image = QImage(120, 100, QImage.Format.Format_RGB32)
    image.fill(color)
    assert image.save(str(path))


def make_project(directory: Path, *, relative_paths: bool = False) -> Project:
    first_path = directory / "หน้า1.png"
    second_path = directory / "หน้า2.png"
    save_image(first_path)
    save_image(second_path)
    first = Page(
        source_path=first_path.name if relative_paths else str(first_path),
        width=120,
        height=100,
    )
    first.blocks.extend(
        [
            TextBlock(
                page_id=first.id,
                bbox=BoundingBox(x=5, y=5, width=20, height=20),
                reading_order=1,
                source_text="one",
            ),
            TextBlock(
                page_id=first.id,
                bbox=BoundingBox(x=35, y=5, width=20, height=20),
                reading_order=2,
                source_text="two",
            ),
        ]
    )
    second = Page(source_path=str(second_path), width=120, height=100)
    return Project(name="ทดสอบ", pages=[first, second])


def wait_for_workflow(qapp, window: MainWindow, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while window.is_busy and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.001)
    qapp.processEvents()
    assert not window.is_busy


def font_path() -> Path:
    return Path(ImageFont.truetype("NotoSans-Regular.ttf", 12).path)


def _contrast_ratio(first: str, second: str) -> float:
    def luminance(value: str) -> float:
        channels = []
        for channel in (QColor(value).redF(), QColor(value).greenF(), QColor(value).blueF()):
            channels.append(
                channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            )
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class DeterministicTranslationProvider:
    async def translate_blocks(self, blocks, context):
        return [
            TranslationResult(id=item.id, translated_text=f"translated:{item.source_text}")
            for item in blocks
        ]


def test_layout_set_project_and_page_navigation(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    project = make_project(tmp_path)

    window.set_project(project)

    assert isinstance(window.centralWidget(), QSplitter)
    assert isinstance(window.navigator_tabs, QTabWidget)
    assert window.splitter.widget(0) is window.navigator_tabs
    assert window.navigator_tabs.widget(0) is window.page_sidebar
    assert window.navigator_tabs.widget(1) is window.block_strip
    assert window.splitter.widget(1) is window.comparison_pane
    assert window.splitter.widget(2) is window.block_editor
    assert window.minimumSizeHint().height() <= window.height()
    assert window.image_viewer.parentWidget().findChild(QLabel).text() == "Original"
    assert window.preview_viewer.parentWidget().findChild(QLabel).text() == "Thai Preview"
    assert window.project is project
    assert window.page_sidebar.count() == 2
    assert window.page_sidebar.currentRow() == 0
    assert not window.block_editor.isEnabled()

    window.select_next_page()
    assert window.page_sidebar.currentRow() == 1
    window.select_previous_page()
    assert window.page_sidebar.currentRow() == 0


def test_block_selection_edit_create_delete_and_navigation(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    project = make_project(tmp_path)
    page = project.pages[0]
    first, second = page.blocks
    window.set_project(project)

    window.image_viewer.block_selected.emit(first.id)
    assert window.block_editor.source_text_edit.toPlainText() == "one"

    window.block_editor.source_text_edit.setPlainText("edited")
    edited = page.blocks[0]
    assert edited.id == first.id
    assert edited.source_text == "edited"

    created = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=65, y=5, width=20, height=20),
        reading_order=3,
    )
    window.image_viewer._add_block(created)
    window.image_viewer.block_created.emit(created)
    assert page.blocks[-1] is created
    assert window.page_sidebar.item(0).text().endswith("3 blocks")
    assert window._current_block_id == created.id
    assert window.previous_block_action.isEnabled()
    assert not window.next_block_action.isEnabled()
    assert window.delete_block_action.isEnabled()
    assert window.move_block_earlier_action.isEnabled()
    assert not window.move_block_later_action.isEnabled()
    assert window.ocr_selected_action.isEnabled()
    assert window.translate_selected_action.isEnabled()

    moved = created.model_copy(update={"bbox": BoundingBox(x=70, y=10, width=25, height=25)})
    window.image_viewer.block_changed.emit(moved)
    assert page.blocks[-1].id == created.id
    assert page.blocks[-1].bbox == moved.bbox

    window.image_viewer.select_block(first.id)
    window._select_block(first.id)
    window.select_next_block()
    assert window.image_viewer.selected_block_id == second.id
    window.select_previous_block()
    assert window.image_viewer.selected_block_id == first.id

    window.image_viewer.select_block(second.id)
    window._select_block(second.id)
    window.image_viewer.delete_selected_block()
    assert all(block.id != second.id for block in page.blocks)
    assert window._current_block_id is None
    assert window.previous_block_action.isEnabled()
    assert window.next_block_action.isEnabled()
    assert not window.delete_block_action.isEnabled()
    assert not window.move_block_earlier_action.isEnabled()
    assert not window.move_block_later_action.isEnabled()
    assert not window.ocr_selected_action.isEnabled()
    assert not window.translate_selected_action.isEnabled()

    window.block_editor.set_block(edited)
    window.block_editor.delete_requested.emit(edited.id)
    assert all(block.id != edited.id for block in page.blocks)
    assert not window.block_editor.isEnabled()
    assert window.page_sidebar.item(0).text().endswith("1 block")

    window.image_viewer.select_block(created.id)
    window._select_block(created.id)
    window.image_viewer.delete_selected_block()
    assert page.blocks == []
    assert window._current_block_id is None
    assert not window.previous_block_action.isEnabled()
    assert not window.next_block_action.isEnabled()
    assert not window.delete_block_action.isEnabled()
    assert not window.move_block_earlier_action.isEnabled()
    assert not window.move_block_later_action.isEnabled()
    assert not window.ocr_selected_action.isEnabled()
    assert not window.translate_selected_action.isEnabled()


def test_ocr_block_strip_selects_and_deletes_blocks(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    first, second = page.blocks
    window = MainWindow()
    window.set_project(project, tmp_path)

    assert set(window.block_strip._cards) == {first.id, second.id}

    window.block_strip._cards[second.id].clicked.emit()
    assert window._current_block_id == second.id
    assert window.block_editor.source_text_edit.toPlainText() == "two"
    assert window.block_strip._cards[second.id].property("selected") is True

    window.block_strip._cards[second.id].delete_button.click()

    assert [block.id for block in page.blocks] == [first.id]
    assert set(window.block_strip._cards) == {first.id}
    assert window.page_sidebar.item(0).text().endswith("1 block")


def test_multi_selection_syncs_primary_and_selected_workflow_ids(
    qapp, tmp_path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    first, second = page.blocks
    window.image_viewer.set_selected_block_ids((second.id, first.id), primary_id=first.id)
    assert window.image_viewer.selected_block_ids == (first.id, second.id)
    assert window._current_block_id == first.id
    assert window.block_strip.selected_block_ids == (first.id, second.id)

    captured = []
    monkeypatch.setattr(window, "_start_ocr", lambda *args: captured.append(args) or True)
    assert window.ocr_selected_block()
    assert captured == [((page.id,), (first.id, second.id), "OCR selected block")]

    captured.clear()
    monkeypatch.setattr(window, "_start_translation", lambda *args: captured.append(args) or True)
    assert window.translate_selected_block()
    assert captured == [((page.id,), (first.id, second.id), "Translate selected block")]


def test_multi_delete_keeps_valid_primary_and_clears_inspector_when_empty(qapp, tmp_path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    first, second = page.blocks
    window.image_viewer.set_selected_block_ids((first.id, second.id), primary_id=second.id)

    window._delete_selected_block()

    assert page.blocks == []
    assert window.image_viewer.selected_block_ids == ()
    assert window._current_block_id is None
    assert not window.block_editor.isEnabled()


def test_partial_delete_promotes_remaining_primary_to_inspector(qapp, tmp_path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    first, second = page.blocks
    window.image_viewer.set_selected_block_ids((first.id, second.id), primary_id=second.id)

    window.image_viewer.delete_block_ids((second.id,))

    assert [block.id for block in page.blocks] == [first.id]
    assert window.image_viewer.selected_block_ids == (first.id,)
    assert window._current_block_id == first.id
    assert window.block_editor.source_text_edit.toPlainText() == "one"


def test_rotated_block_size_edit_stays_inside_image_and_syncs_workspace(qapp, tmp_path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0].model_copy(
        update={
            "bbox": BoundingBox(x=90, y=70, width=20, height=20),
            "rotation_degrees": 45,
        }
    )
    page.blocks = [block]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)

    window.block_editor.width_spin.setValue(30)
    window.block_editor.height_spin.setValue(30)

    updated = page.blocks[0]
    bounds = rotated_bbox_bounds(updated.bbox, updated.rotation_degrees)
    assert bounds.x >= -1e-6
    assert bounds.y >= -1e-6
    assert bounds.x + bounds.width <= page.width + 1e-6
    assert bounds.y + bounds.height <= page.height + 1e-6
    assert window.image_viewer._items[block.id].block == updated
    assert window.block_editor._block == updated
    assert window.block_editor.width_spin.value() == updated.bbox.width
    assert window.block_editor.height_spin.value() == updated.bbox.height


def test_rotated_block_boundary_edit_does_not_recurse_and_saves(qapp, tmp_path) -> None:
    source = tmp_path / "rotated-save.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=100, height=80)
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=10, y=10, width=20, height=20),
        reading_order=1,
        rotation_degrees=15,
    )
    page.blocks = [block]
    window = MainWindow()
    window.set_project(Project(name="rotated-save", pages=[page]), tmp_path)
    window.image_viewer.select_block(block.id)
    updates = []
    window.block_editor.block_changed.disconnect(window._edit_block)

    def guarded_edit(changed: TextBlock) -> None:
        updates.append(changed)
        if len(updates) <= 10:
            window._edit_block(changed)

    window.block_editor.block_changed.connect(guarded_edit)

    window.block_editor.width_spin.setValue(60)
    window.block_editor.height_spin.setValue(89)
    window.save_action.trigger()

    assert len(updates) == 2
    saved = ProjectRepository.load(tmp_path)
    updated = saved.pages[0].blocks[0]
    bounds = rotated_bbox_bounds(updated.bbox, updated.rotation_degrees)
    assert bounds.x >= -1e-6
    assert bounds.y >= -1e-6
    assert bounds.x + bounds.width <= page.width + 1e-6
    assert bounds.y + bounds.height <= page.height + 1e-6
    assert page.blocks[0] == updated
    assert window.image_viewer._items[block.id].block == updated
    assert window.block_editor._block == updated


def test_block_size_edit_preserves_text_undo_when_geometry_needs_no_fit(qapp, tmp_path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    editor = window.block_editor.source_text_edit
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    editor.insertPlainText("!")

    window.block_editor.width_spin.setValue(block.bbox.width + 1)
    QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    assert editor.toPlainText() == block.source_text


def test_thai_input_preserves_order_for_in_bounds_float_bbox(qapp, tmp_path: Path) -> None:
    source = tmp_path / "long-page.png"
    image = QImage(690, 16_000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=690, height=16_000)
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(
            x=203,
            y=2444,
            width=287.7,
            height=160.80000000000018,
        ),
        reading_order=1,
        translated_text="เดิม",
    )
    page.blocks = [block]
    project = Project(name="Thai input", pages=[page])
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)

    editor = window.block_editor.translated_text_edit
    editor.clear()
    editor.moveCursor(QTextCursor.MoveOperation.End)
    for commit in ("ก", "ำ", "ลั", "ง", "ท", "ด", "ส", "อ", "บ"):
        event = QInputMethodEvent()
        event.setCommitString(commit)
        QCoreApplication.sendEvent(editor, event)

    assert editor.toPlainText() == "กำลังทดสอบ"
    assert project.pages[0].blocks[0].translated_text == "กำลังทดสอบ"
    assert editor.textCursor().position() == len("กำลังทดสอบ")


def test_preview_metric_updates_selected_block_runtime_fields(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    window._thai_font_path = font_path()
    starts = []
    monkeypatch.setattr(
        window,
        "_start_background_worker",
        lambda worker, name, progress, completed: starts.append(worker) or True,
    )

    assert window._start_page_preview(page, "Thai preview")
    starts[0].metric.emit(RenderMetric(block.id, 31, (1, 2, 3), (250, 128, 0)))

    assert window.block_editor.thai_font_edit.text() == font_path().name
    assert window.block_editor.effective_font_size_edit.text() == "31 px"
    assert window.block_editor.effective_fill_edit.text() == "#010203"
    assert window.block_editor.effective_stroke_edit.text() == "#FA8000"


def test_preview_metric_is_cleared_when_preview_affecting_edit_changes(
    qapp, tmp_path: Path
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    window._thai_font_path = font_path()
    window._preview_metric_received(RenderMetric(block.id, 31, (1, 2, 3), None))

    window.block_editor.translated_text_edit.setPlainText("changed")

    assert window.block_editor.effective_font_size_edit.text() == "Auto"


def test_native_actions_have_distinct_shortcuts(qapp) -> None:
    window = MainWindow()
    actions = (
        window.open_images_action,
        window.open_image_folder_action,
        window.new_project_action,
        window.import_images_action,
        window.save_action,
        window.run_workflow_action,
        window.cancel_workflow_action,
        window.previous_page_action,
        window.next_page_action,
        window.previous_block_action,
        window.next_block_action,
        window.move_block_earlier_action,
        window.move_block_later_action,
        window.draw_block_action,
        window.delete_block_action,
        window.fit_action,
        window.reset_zoom_action,
        window.zoom_in_action,
        window.zoom_out_action,
        window.split_view_action,
        window.original_view_action,
        window.preview_view_action,
    )
    shortcuts = [action.shortcut().toString() for action in actions]

    assert all(shortcuts)
    assert len(shortcuts) == len(set(shortcuts))
    assert window.toolbar.actions() == [
        window.open_images_action,
        window.save_action,
        window.undo_workflow_action,
        window.run_workflow_action,
        window.export_action,
        window.cancel_workflow_action,
    ]
    scoped_actions = (
        window.open_action,
        window.undo_workflow_action,
        window.translate_folder_action,
        window.configure_translation_action,
        window.export_action,
        window.ocr_selected_action,
        window.ocr_page_action,
        window.ocr_all_action,
        window.confirm_ocr_page_action,
        window.confirm_ocr_all_action,
        window.translate_selected_action,
        window.translate_page_action,
        window.translate_all_action,
        window.reclean_selected_action,
        window.reclean_page_action,
        window.prepare_manual_cleanup_action,
        window.prepare_page_cleanups_action,
    )
    assert all(action.shortcut().isEmpty() for action in scoped_actions)
    assert len({action.text() for action in scoped_actions}) == len(scoped_actions)
    assert window.open_images_action.shortcut().toString() == "Ctrl+O"
    assert window.open_image_folder_action.shortcut().toString() == "Ctrl+Shift+O"
    assert window.toolbar.actions()[0] is window.open_images_action


def test_main_toolbar_has_exact_idle_actions_and_busy_cancel(qapp) -> None:
    window = MainWindow()
    window.show()
    qapp.processEvents()
    expected = [
        window.open_images_action,
        window.save_action,
        window.undo_workflow_action,
        window.run_workflow_action,
        window.export_action,
    ]

    assert [action for action in window.toolbar.actions() if action.isVisible()] == expected
    assert not window.cancel_workflow_action.isVisible()
    assert [action.text() for action in expected] == [
        "Open Images…",
        "Save",
        "Undo Last Workflow",
        "Run Workflow",
        "Export…",
    ]
    for action in (*expected, window.cancel_workflow_action):
        button = window.toolbar.widgetForAction(action)
        assert button is not None
        assert button.minimumSize().width() >= 36
        assert button.minimumSize().height() >= 36
        assert button.accessibleName()

    window._workflow_thread = object()
    window._update_action_states()
    assert window.cancel_workflow_action.isVisible()
    assert [action for action in window.toolbar.actions() if action.isVisible()] == [
        *expected,
        window.cancel_workflow_action,
    ]

    window._workflow_thread = None
    window._update_action_states()
    assert not window.cancel_workflow_action.isVisible()


def test_workspace_focus_order_and_accessible_names(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.progress_dock.show()
    window.show()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(project.pages[0].blocks[0].id)
    qapp.processEvents()

    ordered = (
        window.navigator_tabs,
        window.image_viewer,
        window.block_editor.tabs,
        window.workflow_log,
    )
    assert [widget.accessibleName() for widget in ordered] == [
        "Page and block navigator",
        "Original image",
        "Text block inspector",
        "Activity log",
    ]
    assert window.navigator_tabs.tabBar().accessibleName() == "Page and block navigator tabs"
    assert window.block_editor.tabs.tabBar().accessibleName() == "Text block inspector tabs"
    assert window.navigator_tabs.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.image_viewer.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.block_editor.tabs.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.workflow_log.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert window.block_editor.source_text_edit.tabChangesFocus()
    assert window.block_editor.translated_text_edit.tabChangesFocus()
    assert window.block_editor.note_edit.tabChangesFocus()
    assert window.workflow_log.tabChangesFocus()

    areas = []
    containers = (
        ("navigator", window.navigator_tabs),
        ("viewer", window.image_viewer),
        ("inspector", window.block_editor),
        ("activity", window.progress_dock),
    )
    window.navigator_tabs.setFocus()
    qapp.processEvents()
    for _ in range(100):
        focus = qapp.focusWidget()
        area = next(
            (
                name
                for name, container in containers
                if focus is container or (focus is not None and container.isAncestorOf(focus))
            ),
            None,
        )
        if area is not None and (not areas or areas[-1] != area):
            areas.append(area)
        if areas[:4] == ["navigator", "viewer", "inspector", "activity"]:
            break
        assert focus is not None
        QTest.keyClick(focus, Qt.Key.Key_Tab)
        qapp.processEvents()

    assert areas[:4] == ["navigator", "viewer", "inspector", "activity"]


def test_primary_controls_fit_at_minimum_workspace_size(qapp) -> None:
    window = MainWindow()
    window.resize(1366, 768)
    window.show()
    qapp.processEvents()

    assert window.toolbar.geometry().bottom() <= window.height()
    assert window.centralWidget().geometry().bottom() <= window.height()
    assert all(
        window.toolbar.widgetForAction(action).geometry().bottom() <= window.toolbar.height()
        for action in window.toolbar.actions()
        if window.toolbar.widgetForAction(action) is not None and action.isVisible()
    )
    assert window.open_images_action.isVisible()
    assert window.run_workflow_action.isVisible()
    assert window.export_action.isVisible()


def test_light_studio_tokens_and_status_presentation_are_shared(qapp, tmp_path: Path) -> None:
    required_tokens = {
        "window",
        "panel",
        "panel_alt",
        "ink",
        "muted",
        "rule",
        "canvas",
        "accent",
        "accent_hover",
        "warning",
        "danger",
    }
    assert required_tokens <= COLORS.keys()
    assert all(value in LIGHT_STUDIO_STYLESHEET for value in COLORS.values())
    assert all(
        _contrast_ratio(COLORS["rule"], COLORS[surface]) >= 3.0
        for surface in ("panel", "panel_alt", "window")
    )
    assert _contrast_ratio(COLORS["panel"], COLORS["rule"]) >= 4.5
    assert (
        f"QPushButton:pressed, QToolButton:pressed {{\n"
        f"    background: {COLORS['rule']};\n"
        f"    color: {COLORS['panel']};"
    ) in LIGHT_STUDIO_STYLESHEET
    assert (
        f"QMenuBar::item:selected, QMenuBar::item:hover {{\n"
        f"    background: {COLORS['accent']};\n"
        f"    color: {COLORS['panel']};"
    ) in LIGHT_STUDIO_STYLESHEET
    assert (
        f"QMenu::item:selected, QMenu::item:hover {{\n"
        f"    background: {COLORS['accent']};\n"
        f"    color: {COLORS['panel']};"
    ) in LIGHT_STUDIO_STYLESHEET
    assert "QMenuBar::item:disabled:hover" in LIGHT_STUDIO_STYLESHEET
    assert "QMenu::item:disabled:hover" in LIGHT_STUDIO_STYLESHEET

    path = tmp_path / "status.png"
    save_image(path)
    page = Page(source_path=str(path), width=120, height=100)
    page.blocks = [
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=5, y=5, width=20, height=20),
            reading_order=1,
            source_text="OCR",
            status=BlockStatus.ERROR,
        )
    ]
    project = Project(
        name="status",
        pages=[page],
    )
    window = MainWindow()
    window.set_project(project, tmp_path)
    card = window.block_strip._cards[project.pages[0].blocks[0].id]
    assert "Error" in card.status_label.text()
    assert "⚠" in card.status_label.text()
    assert card.delete_button.minimumSize().width() >= 36
    assert card.delete_button.minimumSize().height() >= 36


def test_view_actions_require_loaded_images_and_idle_state(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    source_actions = (
        window.draw_block_action,
        window.show_source_action,
        window.fit_action,
        window.reset_zoom_action,
        window.zoom_in_action,
        window.zoom_out_action,
    )
    source_view_actions = (window.split_view_action, window.original_view_action)
    view_buttons = tuple(window.view_mode_buttons.values())

    assert all(not action.isEnabled() for action in (*source_actions, *source_view_actions))
    assert not window.preview_view_action.isEnabled()
    assert all(not button.isEnabled() for button in view_buttons)
    assert all(action.isEnabled() for action in window.menuBar().actions())

    missing_page = Page(source_path=str(tmp_path / "missing.png"), width=120, height=100)
    window.set_project(Project(name="missing", pages=[missing_page]), tmp_path)
    assert all(not action.isEnabled() for action in (*source_actions, *source_view_actions))
    assert not window.preview_view_action.isEnabled()

    project = make_project(tmp_path)
    window.set_project(project, tmp_path)
    assert all(action.isEnabled() for action in (*source_actions, *source_view_actions))
    assert not window.preview_view_action.isEnabled()
    assert window.view_mode_buttons["split"].isEnabled()
    assert window.view_mode_buttons["original"].isEnabled()
    assert not window.view_mode_buttons["preview"].isEnabled()

    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    save_image(preview_dir / f"preview-page-{project.pages[0].id}.png")
    window._load_page_preview(project.pages[0])
    window._update_action_states()
    assert window.preview_view_action.isEnabled()
    assert window.view_mode_buttons["preview"].isEnabled()

    window._workflow_thread = object()
    window._update_action_states()
    assert all(
        not action.isEnabled()
        for action in (*source_actions, *source_view_actions, window.preview_view_action)
    )
    assert all(not button.isEnabled() for button in view_buttons)
    window._workflow_thread = None
    window._update_action_states()


def test_block_navigation_disables_at_selected_edges(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first, second = project.pages[0].blocks
    window = MainWindow()
    window.set_project(project, tmp_path)

    assert window.previous_block_action.isEnabled()
    assert window.next_block_action.isEnabled()

    window.image_viewer.select_block(first.id)
    assert not window.previous_block_action.isEnabled()
    assert window.next_block_action.isEnabled()

    window.image_viewer.select_block(second.id)
    assert window.previous_block_action.isEnabled()
    assert not window.next_block_action.isEnabled()


def test_activity_stays_collapsed_on_success_and_opens_on_error(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.JA
    window = MainWindow()
    window.set_project(project)

    assert window.progress_dock.isHidden()
    assert window.run_workflow()
    assert window.progress_dock.isHidden()
    wait_for_workflow(qapp, window)
    assert window.progress_dock.isHidden()

    window._workflow_failed("raw technical error")
    assert not window.progress_dock.isHidden()
    assert "raw technical error" in window.workflow_log.toPlainText()


def test_view_modes_preserve_context_sizes_and_hidden_sync(qapp, tmp_path: Path) -> None:
    source = tmp_path / "focus.png"
    image = QImage(800, 3000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=800, height=3000)
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=100, y=1200, width=200, height=100),
        reading_order=1,
        source_text="focus",
    )
    page.blocks = [block]
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    assert image.save(str(preview_dir / f"preview-page-{page.id}.png"))
    window = MainWindow()
    window.resize(1100, 700)
    window.show()
    window.set_project(Project(name="focus", pages=[page]), tmp_path)
    window.image_viewer.select_block(block.id)
    window.comparison_splitter.setSizes([320, 480])
    window.image_viewer.reset_zoom()
    window.image_viewer.zoom_in()
    window.image_viewer.verticalScrollBar().setValue(700)
    qapp.processEvents()
    split_sizes = window.comparison_splitter.sizes()
    page_id = window._current_page_id
    zoom = window.image_viewer.transform().m11()

    window.set_view_mode("original")
    window.image_viewer.zoom_in()
    qapp.processEvents()

    assert not window.original_pane.isHidden()
    assert window.preview_pane.isHidden()
    assert window.preview_viewer.transform().m11() == pytest.approx(
        window.image_viewer.transform().m11()
    )

    window.set_view_mode("split")
    qapp.processEvents()

    assert not window.original_pane.isHidden()
    assert not window.preview_pane.isHidden()
    assert window.comparison_splitter.sizes() == split_sizes
    assert window._current_page_id == page_id
    assert window._current_block_id == block.id
    assert window.image_viewer.selected_block_id == block.id
    assert window.image_viewer.transform().m11() > zoom
    assert window.split_view_action.isChecked()

    window.preview_view_action.trigger()
    assert window.original_pane.isHidden()
    assert not window.preview_pane.isHidden()
    assert window.preview_view_action.isChecked()


def test_ui_settings_round_trip_and_focus_mode_is_not_restored(qapp, tmp_path: Path) -> None:
    settings_path = tmp_path / "ui.ini"
    first_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    first = MainWindow(settings=first_settings)
    first.show()
    first.splitter.setSizes([250, 700, 380])
    first.comparison_splitter.setSizes([310, 490])
    first.navigator_tabs.setCurrentIndex(1)
    first.block_editor.tabs.setCurrentIndex(2)
    first.progress_dock.show()
    first.set_view_mode("preview")
    first._save_ui_state()
    saved_main = [int(value) for value in first_settings.value("main_splitter_sizes")]
    saved_comparison = [int(value) for value in first_settings.value("comparison_splitter_sizes")]
    first.close()

    restored = MainWindow(settings=QSettings(str(settings_path), QSettings.Format.IniFormat))
    restored.show()
    qapp.processEvents()

    assert restored.splitter.sizes() == saved_main
    assert restored.comparison_splitter.sizes() == saved_comparison
    assert restored.navigator_tabs.currentIndex() == 1
    assert restored.block_editor.tabs.currentIndex() == 2
    assert not restored.progress_dock.isHidden()
    assert not restored.original_pane.isHidden()
    assert not restored.preview_pane.isHidden()
    assert restored.split_view_action.isChecked()


def test_invalid_ui_settings_fall_back_safely(qapp, tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "invalid.ini"), QSettings.Format.IniFormat)
    settings.setValue("main_splitter_sizes", ["bad", 0])
    settings.setValue("comparison_splitter_sizes", [0, "bad"])
    settings.setValue("navigator_tab", 99)
    settings.setValue("inspector_tab", "bad")
    settings.setValue("activity_visible", "not-a-bool")

    window = MainWindow(settings=settings)

    assert len(window.splitter.sizes()) == 3
    assert all(size > 0 for size in window.splitter.sizes())
    assert len(window.comparison_splitter.sizes()) == 2
    assert all(size > 0 for size in window.comparison_splitter.sizes())
    assert window.navigator_tabs.currentIndex() == 0
    assert window.block_editor.tabs.currentIndex() == 0
    assert window.progress_dock.isHidden()
    assert not window.original_pane.isHidden()
    assert not window.preview_pane.isHidden()


def test_confirm_ocr_current_page_updates_only_eligible_blocks(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    page.blocks[0].status = BlockStatus.OCR_COMPLETE
    page.blocks[1].status = BlockStatus.ERROR
    skipped_statuses = (
        BlockStatus.DETECTED,
        BlockStatus.LANGUAGE_REVIEW_REQUIRED,
        BlockStatus.OCR_REVIEWED,
        BlockStatus.TRANSLATED,
        BlockStatus.TRANSLATION_REVIEWED,
    )
    for index, status in enumerate(skipped_statuses, 3):
        page.blocks.append(
            TextBlock(
                page_id=page.id,
                bbox=BoundingBox(x=index * 5, y=40, width=4, height=4),
                reading_order=index,
                source_text=status.value,
                status=status,
            )
        )
    page.blocks.append(
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=50, y=50, width=4, height=4),
            reading_order=8,
            source_text="eligible",
            status=BlockStatus.OCR_COMPLETE,
        )
    )
    other_page_block = TextBlock(
        page_id=project.pages[1].id,
        bbox=BoundingBox(x=5, y=5, width=4, height=4),
        reading_order=1,
        status=BlockStatus.OCR_COMPLETE,
    )
    project.pages[1].blocks = [other_page_block]
    before = {
        block.id: (block.source_text, block.reading_order, block.status, block.updated_at)
        for block in page.blocks
    }
    eligible_ids = {block.id for block in page.blocks if block.status is BlockStatus.OCR_COMPLETE}
    window = MainWindow()
    window.set_project(project)
    selected_id = page.blocks[0].id
    window.image_viewer.select_block(selected_id)

    assert window.confirm_ocr_page_action.text() == "Confirm OCR for Current Page"
    assert window.confirm_ocr_page_action.isEnabled()
    assert window.confirm_ocr_current_page()

    assert [block.id for block in page.blocks] == list(before)
    for block in page.blocks:
        source_text, reading_order, status, updated_at = before[block.id]
        assert block.source_text == source_text
        assert block.reading_order == reading_order
        if block.id in eligible_ids:
            assert block.status is BlockStatus.OCR_REVIEWED
            assert block.updated_at > updated_at
        else:
            assert block.status is status
            assert block.updated_at == updated_at
    assert other_page_block.status is BlockStatus.OCR_COMPLETE
    assert window.image_viewer.selected_block_id == selected_id
    assert window.block_editor.status_combo.currentData() == BlockStatus.OCR_REVIEWED.value
    assert not window.confirm_ocr_page_action.isEnabled()
    assert "Confirmed OCR for 2 blocks" in window.statusBar().currentMessage()
    unchanged = project.model_dump_json()
    assert not window.confirm_ocr_current_page()
    assert project.model_dump_json() == unchanged


def test_confirm_ocr_all_pages_updates_only_eligible_blocks(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    current = project.pages[0].blocks[0]
    current.status = BlockStatus.OCR_COMPLETE
    skipped = project.pages[0].blocks[1]
    skipped.status = BlockStatus.ERROR
    other = TextBlock(
        page_id=project.pages[1].id,
        bbox=BoundingBox(x=5, y=5, width=20, height=20),
        reading_order=1,
        status=BlockStatus.OCR_COMPLETE,
    )
    project.pages[1].blocks = [other]
    before = {
        block.id: (block.status, block.updated_at)
        for page in project.pages
        for block in page.blocks
    }
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(current.id)

    assert window.confirm_ocr_all_action.text() == "Confirm OCR for All Pages"
    assert window.confirm_ocr_all_action.isEnabled()
    assert window.confirm_ocr_all_pages()

    for page in project.pages:
        for block in page.blocks:
            status, updated_at = before[block.id]
            if status is BlockStatus.OCR_COMPLETE:
                assert block.status is BlockStatus.OCR_REVIEWED
                assert block.updated_at > updated_at
            else:
                assert block.status is status
                assert block.updated_at == updated_at
    assert window.image_viewer.selected_block_id == current.id
    assert window.block_editor.status_combo.currentData() == BlockStatus.OCR_REVIEWED.value
    assert not window.confirm_ocr_page_action.isEnabled()
    assert not window.confirm_ocr_all_action.isEnabled()
    assert "Confirmed OCR for 2 blocks on all pages." in window.statusBar().currentMessage()
    unchanged = project.model_dump_json()
    assert not window.confirm_ocr_all_pages()
    assert project.model_dump_json() == unchanged


def test_confirm_ocr_then_direct_translate_prompts_and_continues(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.EN
    block = project.pages[0].blocks[0]
    block.status = BlockStatus.OCR_COMPLETE
    configuration = ProviderConfiguration(
        provider="ollama", model="test", base_url="http://localhost"
    )
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(block.id)

    class AcceptedDialog:
        def __init__(self, received, parent):
            assert received is None
            assert parent is window

        def exec(self):
            return QDialog.DialogCode.Accepted

        def configuration(self):
            return configuration

    monkeypatch.setattr("app.ui.main_window.TranslationProviderDialog", AcceptedDialog)
    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda received: DeterministicTranslationProvider(),
    )

    assert window.confirm_ocr_current_page()
    assert window.translate_selected_action.isEnabled()
    assert window.translate_page_action.isEnabled()
    assert window.translate_all_action.isEnabled()
    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)

    translated = window.project.pages[0].blocks[0]
    assert window.translation_configuration == configuration
    assert translated.status is BlockStatus.TRANSLATED
    assert translated.translated_text == "translated:one"


def test_direct_translate_cancelled_configuration_starts_no_worker(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    block = project.pages[0].blocks[0]
    block.status = BlockStatus.OCR_REVIEWED
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(block.id)

    class RejectedDialog:
        def __init__(self, configuration, parent):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr("app.ui.main_window.TranslationProviderDialog", RejectedDialog)
    monkeypatch.setattr(
        window,
        "_start_workflow",
        lambda *args, **kwargs: pytest.fail("cancelled configuration started a worker"),
    )

    assert window.translate_selected_action.isEnabled()
    assert not window.translate_selected_block()
    assert window.translation_configuration is None
    assert not window.is_busy


def test_move_block_actions_normalize_order_preserve_ids_and_selection(
    qapp, tmp_path: Path
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    first_id, second_id = (block.id for block in page.blocks)
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(second_id)

    assert window.move_block_earlier_action.isEnabled()
    assert not window.move_block_later_action.isEnabled()
    window.move_block_earlier()

    assert [block.id for block in page.blocks] == [second_id, first_id]
    assert [block.reading_order for block in page.blocks] == [1, 2]
    assert window.image_viewer.selected_block_id == second_id
    assert not window.move_block_earlier_action.isEnabled()
    assert window.move_block_later_action.isEnabled()

    window.move_block_later()
    assert [block.id for block in page.blocks] == [first_id, second_id]
    assert [block.reading_order for block in page.blocks] == [1, 2]
    assert {block.id for block in page.blocks} == {first_id, second_id}
    assert window.image_viewer.selected_block_id == second_id


def test_translation_configuration_is_runtime_only_and_dialog_is_testable(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    config = ProviderConfiguration(
        provider="ollama",
        base_url="http://localhost:11434",
        model="local-test-model",
    )
    window = MainWindow()

    class AcceptedDialog:
        def __init__(self, configuration, parent):
            assert configuration is None
            assert parent is window

        def exec(self):
            return QDialog.DialogCode.Accepted

        def configuration(self):
            return config

    monkeypatch.setattr("app.ui.main_window.TranslationProviderDialog", AcceptedDialog)
    assert window._configure_translation_provider()

    assert window.translation_configuration == config
    assert "ollama / local-test-model" in window.workflow_log.toPlainText()
    project = make_project(tmp_path)
    window.set_project(project)
    assert "local-test-model" not in project.model_dump_json()
    window.set_translation_configuration(None)
    assert window.translation_configuration is None


def test_iopaint_configuration_is_runtime_only(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    configuration = IOPaintConfiguration(
        executable="/opt/iopaint/bin/iopaint",
        model="lama",
        device="cpu",
        operation_timeout_seconds=420,
    )

    window.set_iopaint_configuration(configuration)

    assert window.iopaint_configuration == configuration
    project = make_project(tmp_path)
    window.set_project(project, tmp_path)
    ProjectRepository.save(project, tmp_path)
    serialized = (tmp_path / "project.json").read_text(encoding="utf-8")
    assert "/opt/iopaint" not in serialized
    assert "operation_timeout_seconds" not in serialized


def test_iopaint_selected_and_page_cleanup_start_scoped_workers(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    page.blocks[0].translated_text = "หนึ่ง"
    window = MainWindow()
    configuration = IOPaintConfiguration(operation_timeout_seconds=420)
    window.set_iopaint_configuration(configuration)
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(page.blocks[0].id)
    starts = []
    monkeypatch.setattr(
        window,
        "_start_background_worker",
        lambda worker, name, progress, completed: (
            starts.append((worker, name, progress, completed)) or True
        ),
    )

    assert window.iopaint_clean_selected_block()
    assert window.iopaint_clean_current_page()

    selected, page_job = starts
    assert isinstance(selected[0], ImageCleanupWorker)
    assert selected[1] == "IOPaint clean selected block"
    assert selected[0]._block_id == page.blocks[0].id
    assert selected[0]._service._configuration == configuration
    assert isinstance(page_job[0], PageImageCleanupWorker)
    assert page_job[1] == "IOPaint clean current page"
    assert page_job[0]._block_ids == tuple(block.id for block in page.blocks)
    assert page_job[0]._service._configuration == configuration


def test_iopaint_selected_subset_uses_page_worker_with_exact_ids(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    page.blocks.append(
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=65, y=5, width=20, height=20),
            reading_order=3,
        )
    )
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.set_selected_block_ids((page.blocks[1].id, page.blocks[0].id))
    starts = []
    monkeypatch.setattr(
        window,
        "_start_background_worker",
        lambda worker, name, progress, completed: starts.append((worker, name)) or True,
    )

    assert window.iopaint_clean_selected_block()

    worker, name = starts[0]
    assert isinstance(worker, PageImageCleanupWorker)
    assert worker._block_ids == (page.blocks[0].id, page.blocks[1].id)
    assert page.blocks[2].id not in worker._block_ids
    assert name == "IOPaint clean selected block"


def test_selection_is_reset_when_main_window_changes_page(qapp, tmp_path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project, tmp_path)
    page = project.pages[0]
    window.image_viewer.set_selected_block_ids(tuple(block.id for block in page.blocks))
    assert window.image_viewer.selected_block_ids

    window.select_next_page()

    assert window.image_viewer.selected_block_ids == ()
    assert window._current_block_id is None
    assert not window.block_editor.isEnabled()


def test_iopaint_page_completion_reports_failures_and_keeps_successes(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    window = MainWindow()
    warnings = []
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )
    result = PageCleanupResult(
        paths=(tmp_path / "clean.png",),
        issues=(CleanupIssue(uuid4(), "RuntimeError: failed"),),
    )

    window._iopaint_page_cleanup_completed(result)

    log = window.workflow_log.toPlainText()
    assert "IOPaint cleanup saved" in log
    assert "RuntimeError: failed" in log
    assert (
        "IOPaint cleaned 1 block on the current page with 1 issue."
        in window.statusBar().currentMessage()
    )
    assert "Cleanup failed for 1 block; original images were preserved." in warnings[0][2]
    assert len(warnings) == 1


def test_language_precedence_and_provider_label_do_not_load_models(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    project.settings.default_source_language = SourceLanguage.EN
    page.source_language = SourceLanguage.KO
    block.source_language = SourceLanguage.ZH_HANT
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.block_selected.emit(block.id)

    assert window.project_language_combo.currentData() == SourceLanguage.EN.value
    assert window.page_language_combo.currentData() == SourceLanguage.KO.value
    assert "zh-Hant" in window.effective_provider_label.text()
    assert "mock-ocr" in window.effective_provider_label.text()

    block.source_language = SourceLanguage.AUTO
    window.image_viewer.block_selected.emit(block.id)
    assert "review required" in window.effective_provider_label.text()
    block.source_language = None
    window.image_viewer.block_selected.emit(block.id)
    assert "ko" in window.effective_provider_label.text()
    window.page_language_combo.setCurrentIndex(window.page_language_combo.findData(None))
    assert page.source_language is None
    assert "en" in window.effective_provider_label.text()
    window.project_language_combo.setCurrentIndex(
        window.project_language_combo.findData(SourceLanguage.JA.value)
    )
    assert project.settings.default_source_language is SourceLanguage.JA

    monkeypatch.setattr(
        "app.ui.main_window.MangaOcrProvider",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model instantiated")),
    )
    window.ocr_mode_combo.setCurrentIndex(window.ocr_mode_combo.findData("installed-local"))
    assert "manga-ocr" in window.effective_provider_label.text()


def test_installed_ocr_router_shares_paddle_detector_and_mock_stays_mock(qapp) -> None:
    window = MainWindow()
    window.ocr_mode_combo.setCurrentIndex(window.ocr_mode_combo.findData("installed-local"))

    detector, router = window._build_ocr_providers()

    assert router.provider_name_for(SourceLanguage.JA) == "manga-ocr"
    for language in (
        SourceLanguage.EN,
        SourceLanguage.KO,
        SourceLanguage.ZH_HANS,
        SourceLanguage.ZH_HANT,
    ):
        assert router.provider_name_for(language) == "paddleocr"
    assert detector is router.provider_for(SourceLanguage.EN)

    window.ocr_mode_combo.setCurrentIndex(window.ocr_mode_combo.findData("mock"))
    mock_detector, mock_router = window._build_ocr_providers()

    assert isinstance(mock_detector, MockTextDetectionProvider)
    assert mock_detector is not mock_router.provider_for(SourceLanguage.EN)


def test_create_and_import_helpers_replace_project_only_on_success(qapp, tmp_path: Path) -> None:
    first = tmp_path / "หน้า1.png"
    second = tmp_path / "หน้า2.png"
    save_image(first)
    save_image(second)
    window = MainWindow()
    project_dir = tmp_path / "โปรเจกต์"

    assert not window.create_project(project_dir, "  ", [first])
    assert window.project is None
    assert window.create_project(
        project_dir,
        " เรื่อง ",
        [first],
        SourceLanguage.KO,
        ReadingOrderPreset.WEBTOON_VERTICAL,
        copy_sources=False,
    )
    created = window.project
    assert created is not None
    assert created.name == "เรื่อง"
    assert created.settings.default_source_language is SourceLanguage.KO
    assert created.settings.default_reading_order is ReadingOrderPreset.WEBTOON_VERTICAL
    assert Path(created.pages[0].source_path) == first.resolve()

    assert not window.import_images(tmp_path / "missing.png")
    assert window.project is created
    assert window.import_images(second, copy_sources=True)
    assert window.project is not created
    assert [Path(page.source_path).name for page in window.project.pages] == [
        "หน้า1.png",
        "หน้า2.png",
    ]


def test_save_open_helpers_and_relative_image_path(qapp, tmp_path: Path) -> None:
    project_dir = tmp_path / "โปรเจกต์"
    project_dir.mkdir()
    project = make_project(project_dir, relative_paths=True)
    ProjectRepository.save(project, project_dir)
    window = MainWindow()

    assert window.open_project(project_dir)
    assert window.project is not None
    assert window.project.id == project.id
    window.project.name = "แก้แล้ว"
    assert window.save_project()
    assert ProjectRepository.load(project_dir).name == "แก้แล้ว"


def test_export_worker_progress_completion_and_source_project_unchanged(
    qapp, tmp_path: Path
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = make_project(project_dir, relative_paths=True)
    project.pages[0].blocks[0].translated_text = "ข้อความภาษาไทยที่ยาวมากและล้นกรอบแน่นอน"
    project.pages[1].source_path = "missing.png"
    ProjectRepository.save(project, project_dir)
    original_project = project.model_dump_json()
    source_path = project_dir / project.pages[0].source_path
    original_source = source_path.read_bytes()
    window = MainWindow()
    window.set_project(project, project_dir)
    output_dir = tmp_path / "output"

    assert window.export_action.isEnabled()
    assert window.start_export(output_dir, font_path())
    assert window.is_busy
    assert not window.export_action.isEnabled()
    assert not window.splitter.isEnabled()
    wait_for_workflow(qapp, window)

    assert window.project is project
    assert project.model_dump_json() == original_project
    assert source_path.read_bytes() == original_source
    assert (output_dir / "project-export.json").is_file()
    log = window.workflow_log.toPlainText()
    assert "Export 1/2" in log
    assert "project-export.json" in log
    assert "Export completed with 1 issue and 1 overflow warning." in window.progress_label.text()
    assert window.export_action.isEnabled()


def test_export_requires_saved_project_and_native_dialog_uses_white_default(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)

    assert not window.export_action.isEnabled()
    assert not window.start_export(tmp_path / "out", font_path())
    assert not window.is_busy

    project_dir = tmp_path / "saved"
    ProjectRepository.save(project, project_dir)
    window.set_project(project, project_dir)
    selected_font = font_path()
    calls = []
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "dialog-output"),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(selected_font), "Fonts"),
    )
    item_answers = iter((("Text", True), ("None", True)))
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getItem",
        lambda *args, **kwargs: next(item_answers),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getText",
        lambda *args, **kwargs: (kwargs["text"], True),
    )
    monkeypatch.setattr(
        window,
        "start_export",
        lambda output, font, background_color="white", **options: (
            calls.append((Path(output), Path(font), background_color, options)) or True
        ),
    )

    window._choose_export()

    assert calls == [
        (
            tmp_path / "dialog-output",
            selected_font,
            "white",
            {
                "watermark_text": "แปลหลังเลิกงาน",
                "watermark_logo_path": None,
                "banner_path": None,
                "banner_position": "end",
            },
        )
    ]


def test_export_dialog_forwards_logo_and_last_banner(qapp, tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = make_project(project_dir)
    window = MainWindow()
    window.set_project(project, project_dir)
    window._thai_font_path = font_path()
    logo = tmp_path / "logo.png"
    banner = tmp_path / "banner.webp"
    choices = iter(((str(logo), "Images"), (str(banner), "Images")))
    item_answers = iter((("Logo image", True), ("Last page", True)))
    calls = []
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "out"),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: next(choices),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getItem",
        lambda *args, **kwargs: next(item_answers),
    )
    monkeypatch.setattr(
        window,
        "start_export",
        lambda output, font, **options: calls.append((Path(output), Path(font), options)) or True,
    )

    window._choose_export()

    assert calls == [
        (
            tmp_path / "out",
            window._thai_font_path,
            {
                "watermark_text": None,
                "watermark_logo_path": str(logo),
                "banner_path": str(banner),
                "banner_position": "end",
            },
        )
    ]


def test_export_dialog_cancel_preserves_idle_state(qapp, tmp_path: Path, monkeypatch) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project, tmp_path)
    window._thai_font_path = font_path()
    calls = []
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "out"),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getItem",
        lambda *args, **kwargs: ("Text", False),
    )
    monkeypatch.setattr(window, "start_export", lambda *args, **kwargs: calls.append(True))

    window._choose_export()

    assert calls == []
    assert not window.is_busy


def test_export_failure_and_cancellation_are_visible(qapp, tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = make_project(project_dir)
    window = MainWindow()
    window.set_project(project, project_dir)
    original = project.model_dump_json()

    assert window.start_export(tmp_path / "failure", tmp_path / "missing.ttf")
    wait_for_workflow(qapp, window)
    assert "Export failed" in window.workflow_log.toPlainText()
    assert project.model_dump_json() == original

    started = threading.Event()

    class CancellableExportWorker(QObject):
        progress = Signal(object)
        completed = Signal(object)
        cancelled = Signal()
        failed = Signal(str)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self._stop = threading.Event()

        def run(self):
            started.set()
            while not self._stop.wait(0.001):
                pass
            self.cancelled.emit()
            self.finished.emit()

        def cancel(self):
            self._stop.set()

    monkeypatch.setattr("app.ui.main_window.ExportWorker", CancellableExportWorker)
    window.image_viewer.select_block(project.pages[0].blocks[1].id)
    assert window.move_block_earlier_action.isEnabled()
    assert window.start_export(tmp_path / "cancel", font_path())
    assert started.wait(1)
    assert not window.move_block_earlier_action.isEnabled()
    assert not window.move_block_later_action.isEnabled()
    window.cancel_workflow()
    wait_for_workflow(qapp, window)

    assert "Export cancelled." in window.workflow_log.toPlainText()
    assert project.model_dump_json() == original


def test_invalid_project_and_image_report_error_without_crash(qapp, tmp_path: Path) -> None:
    window = MainWindow()

    assert not window.open_project(tmp_path / "missing")
    assert "Could not open project" in window.statusBar().currentMessage()

    broken_page = Page(source_path=str(tmp_path / "missing.png"), width=20, height=20)
    window.set_project(Project(name="broken", pages=[broken_page]))
    assert "Could not load image" in window.statusBar().currentMessage()
    assert not window.block_editor.isEnabled()


def test_configured_generic_workflow_runs_ocr_only_and_not_saved_file(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    image = project_dir / "page.png"
    save_image(image)
    project = Project(
        name="workflow",
        pages=[Page(source_path=image.name, width=120, height=100)],
    )
    project.settings.default_source_language = SourceLanguage.JA
    ProjectRepository.save(project, project_dir)
    saved = (project_dir / "project.json").read_bytes()
    window = MainWindow()
    window.set_project(project, project_dir)
    monkeypatch.setattr(
        window,
        "_build_translation_provider",
        lambda: pytest.fail("generic workflow constructed a translation provider"),
    )
    window.set_translation_configuration(
        ProviderConfiguration(provider="ollama", model="test", base_url="http://localhost")
    )

    assert window.run_workflow_action.text() == "Run Workflow"
    assert window.run_workflow()
    assert window.is_busy
    assert not window.open_images_action.isEnabled()
    assert not window.run_workflow_action.isEnabled()
    assert not window.save_action.isEnabled()
    assert window.cancel_workflow_action.isEnabled()
    assert not window.splitter.isEnabled()
    assert not window.page_sidebar.isEnabled()
    assert not window.image_viewer.isEnabled()
    assert not window.block_editor.isEnabled()
    assert not window.project_language_combo.isEnabled()
    assert not window.page_language_combo.isEnabled()
    assert not window.ocr_mode_combo.isEnabled()
    assert not window.reclean_selected_action.isEnabled()
    assert not window.reclean_page_action.isEnabled()
    assert window.project is project
    wait_for_workflow(qapp, window)

    assert window.project is not project
    block = window.project.pages[0].blocks[0]
    assert block.status is BlockStatus.OCR_COMPLETE
    assert block.source_text == "おかえり"
    assert block.translated_text == ""
    assert "OCR 1/1" in window.workflow_log.toPlainText()
    assert "Translation 1/1" not in window.workflow_log.toPlainText()
    assert "Workflow completed with 0 issues." in window.progress_label.text()
    assert (project_dir / "project.json").read_bytes() == saved
    assert window.run_workflow_action.isEnabled()
    assert window.open_images_action.isEnabled()
    assert not window.cancel_workflow_action.isEnabled()
    assert window.splitter.isEnabled()
    assert window.page_sidebar.isEnabled()
    assert window.image_viewer.isEnabled()
    assert window.project_language_combo.isEnabled()
    assert window.page_language_combo.isEnabled()
    assert window.ocr_mode_combo.isEnabled()
    assert window.reclean_page_action.isEnabled()


def test_run_workflow_without_configuration_delegates_to_ocr_only(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)
    captured = []
    monkeypatch.setattr(
        "app.ui.main_window.TranslationProviderDialog",
        lambda *args, **kwargs: pytest.fail("generic workflow opened the provider dialog"),
    )
    monkeypatch.setattr(
        window,
        "_build_translation_provider",
        lambda: pytest.fail("generic workflow constructed a translation provider"),
    )
    monkeypatch.setattr(window, "_start_ocr", lambda *args: captured.append(args) or True)

    assert window.run_workflow_action.isEnabled()
    assert window.run_workflow()
    assert window.translation_configuration is None
    assert captured == [(None, None, "Workflow")]


def test_run_workflow_without_configuration_cannot_translate_mock_ocr(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.EN
    window = MainWindow()
    window.set_project(project)
    monkeypatch.setattr(
        "app.ui.main_window.TranslationProviderDialog",
        lambda *args, **kwargs: pytest.fail("generic workflow opened the provider dialog"),
    )
    monkeypatch.setattr(
        window,
        "_build_translation_provider",
        lambda: pytest.fail("generic workflow constructed a translation provider"),
    )

    assert window.run_workflow_action.isEnabled()
    assert window.run_workflow()
    wait_for_workflow(qapp, window)

    assert window.translation_configuration is None
    assert not window.is_busy
    assert all(block.status is BlockStatus.OCR_COMPLETE for block in window.project.pages[0].blocks)
    assert all(not block.translated_text for block in window.project.pages[0].blocks)


def test_run_workflow_routes_opted_in_codex_images_to_folder_path(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    window = MainWindow()
    window.set_project(make_project(tmp_path), tmp_path)
    window.set_translation_configuration(
        ProviderConfiguration(provider="codex-cli", model="default", uploads_images=True)
    )
    calls = []
    monkeypatch.setattr(window, "translate_folder_images", lambda: calls.append(True) or True)
    monkeypatch.setattr(
        window,
        "_build_ocr_providers",
        lambda: pytest.fail("Codex image workflow built OCR providers"),
    )

    assert window.run_workflow()
    assert calls == [True]


def test_completed_workflows_can_be_undone_in_order_and_persisted(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    ProjectRepository.save(project, tmp_path)
    page_id = project.pages[0].id
    block_id = project.pages[0].blocks[0].id
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    preview = preview_dir / f"preview-page-{page_id}.png"
    save_image(preview)
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block_id)

    first = project.model_copy(deep=True)
    first.pages[0].blocks[0].translated_text = "first"
    window._workflow_completed(WorkflowResult(project=first))
    second = first.model_copy(deep=True)
    second.pages[0].blocks[0].translated_text = "second"
    window._workflow_completed(WorkflowResult(project=second))

    assert window.undo_workflow_action.isEnabled()
    assert window.undo_last_workflow()
    assert window.project.pages[0].blocks[0].translated_text == "first"
    assert ProjectRepository.load(tmp_path).pages[0].blocks[0].translated_text == "first"
    assert window.image_viewer.selected_block_id == block_id
    assert not preview.exists()

    assert window.undo_last_workflow()
    assert window.project.pages[0].blocks[0].translated_text == ""
    assert ProjectRepository.load(tmp_path).pages[0].blocks[0].translated_text == ""
    assert not window.undo_workflow_action.isEnabled()
    assert not window.undo_last_workflow()


def test_cancel_failure_and_unchanged_completion_add_no_workflow_undo(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)

    window._workflow_cancelled()
    window._workflow_failed("expected")
    window._workflow_completed(WorkflowResult(project=project.model_copy(deep=True)))

    assert window._workflow_undo_history == []
    assert not window.undo_workflow_action.isEnabled()


def test_undo_shortcut_delegates_to_text_but_visible_action_undoes_workflow(
    qapp, tmp_path: Path
) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)
    block_id = project.pages[0].blocks[0].id
    window.image_viewer.select_block(block_id)
    changed = project.model_copy(deep=True)
    changed.pages[0].blocks[0].translated_text = "workflow translation"
    window._workflow_completed(WorkflowResult(project=changed))
    window.show()
    editor = window.block_editor.source_text_edit
    editor.setFocus()
    editor.moveCursor(editor.textCursor().MoveOperation.End)
    editor.insertPlainText("!")
    qapp.processEvents()

    assert window.undo_workflow_shortcut.key().toString() == "Ctrl+Z"
    QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.toPlainText() == "one"
    assert window.undo_workflow_action.isEnabled()

    assert window.undo_workflow_action in window.toolbar.actions()
    window.undo_workflow_action.trigger()
    assert window.project.pages[0].blocks[0].translated_text == ""
    assert not window.undo_workflow_action.isEnabled()


def test_ctrl_z_undoes_workflow_outside_text_editor(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)
    changed = project.model_copy(deep=True)
    changed.pages[0].blocks[0].translated_text = "workflow translation"
    window._workflow_completed(WorkflowResult(project=changed))
    window.show()
    window.image_viewer.setFocus()
    qapp.processEvents()

    QTest.keyClick(
        window.image_viewer,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert window.project.pages[0].blocks[0].translated_text == ""
    assert not window.undo_workflow_action.isEnabled()


def test_setting_different_project_clears_workflow_undo(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)
    changed = project.model_copy(deep=True)
    changed.pages[0].blocks[0].translated_text = "changed"
    window._workflow_completed(WorkflowResult(project=changed))
    assert window.undo_workflow_action.isEnabled()

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    window.set_project(make_project(other_dir), other_dir)

    assert window._workflow_undo_history == []
    assert not window.undo_workflow_action.isEnabled()


def test_mock_ocr_selected_page_and_all_scopes_preserve_selection(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.JA
    second = project.pages[1]
    second.blocks = [
        TextBlock(
            page_id=second.id,
            bbox=BoundingBox(x=5, y=5, width=20, height=20),
            reading_order=1,
        )
    ]
    window = MainWindow()
    window.set_project(project)
    selected_id = project.pages[0].blocks[0].id
    untouched_id = project.pages[0].blocks[1].id
    second_id = second.blocks[0].id
    window.image_viewer.select_block(selected_id)

    assert window.ocr_selected_block()
    wait_for_workflow(qapp, window)
    assert window.image_viewer.selected_block_id == selected_id
    blocks = {block.id: block for page in window.project.pages for block in page.blocks}
    assert blocks[selected_id].status is BlockStatus.OCR_COMPLETE
    assert blocks[selected_id].ocr_provider == "mock-ocr"
    assert blocks[untouched_id].status is BlockStatus.DETECTED
    assert blocks[second_id].status is BlockStatus.DETECTED

    assert window.ocr_current_page()
    wait_for_workflow(qapp, window)
    blocks = {block.id: block for page in window.project.pages for block in page.blocks}
    assert blocks[untouched_id].ocr_provider == "mock-ocr"
    assert blocks[second_id].status is BlockStatus.DETECTED

    assert window.ocr_all_pages()
    wait_for_workflow(qapp, window)
    blocks = {block.id: block for page in window.project.pages for block in page.blocks}
    assert blocks[second_id].ocr_provider == "mock-ocr"
    assert blocks[second_id].status is BlockStatus.OCR_COMPLETE


def test_mock_ocr_selected_blocks_preserve_exact_selection_and_primary(
    qapp, tmp_path: Path
) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.JA
    page = project.pages[0]
    page.blocks.append(
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=65, y=5, width=20, height=20),
            reading_order=3,
            source_text="three",
        )
    )
    first, second, unselected = page.blocks
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.set_selected_block_ids((first.id, second.id), primary_id=second.id)
    expected_selection = window.image_viewer.selected_block_ids
    expected_primary = window.image_viewer.selected_block_id

    assert window.ocr_selected_block()
    wait_for_workflow(qapp, window)

    assert window.image_viewer.selected_block_ids == expected_selection
    assert window.image_viewer.selected_block_id == expected_primary
    blocks = {block.id: block for block in window.project.pages[0].blocks}
    assert blocks[first.id].status is BlockStatus.OCR_COMPLETE
    assert blocks[second.id].status is BlockStatus.OCR_COMPLETE
    assert blocks[unselected.id].status is BlockStatus.DETECTED


def test_missing_openai_secret_is_visible_redacted_and_starts_no_worker(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    project.settings.default_source_language = SourceLanguage.EN
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(project.pages[0].blocks[0].id)
    config = ProviderConfiguration(
        provider="openai-compatible",
        base_url="https://translator.example/v1",
        model="configured-model",
        api_key_env="MISSING_TRANSLATION_KEY",
    )
    monkeypatch.delenv("MISSING_TRANSLATION_KEY", raising=False)
    window.set_translation_configuration(config)

    assert not window.translate_selected_block()
    assert not window.is_busy
    log = window.workflow_log.toPlainText()
    assert "MISSING_TRANSLATION_KEY" in log
    assert "Could not start translation" in log


def test_configured_openai_and_ollama_providers_are_constructed_without_network(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    block = project.pages[0].blocks[0]
    block.status = BlockStatus.OCR_REVIEWED
    project.settings.default_source_language = SourceLanguage.EN
    window = MainWindow()
    window.set_project(project)
    window.image_viewer.select_block(block.id)
    constructed = []

    class DeterministicProvider:
        async def translate_blocks(self, blocks, context):
            return [
                TranslationResult(id=item.id, translated_text=f"translated:{item.source_text}")
                for item in blocks
            ]

    def openai_provider(configuration, *, api_key):
        constructed.append((configuration.provider, api_key))
        return DeterministicProvider()

    def ollama_provider(configuration):
        constructed.append((configuration.provider, None))
        return DeterministicProvider()

    monkeypatch.setattr("app.ui.main_window.OpenAICompatibleTranslationProvider", openai_provider)
    monkeypatch.setattr("app.ui.main_window.OllamaTranslationProvider", ollama_provider)
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "do-not-log-this-secret")
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="openai-compatible",
            base_url="https://translator.example/v1",
            model="configured-openai",
            api_key_env="TEST_TRANSLATION_KEY",
        )
    )

    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)
    assert constructed[-1] == ("openai-compatible", "do-not-log-this-secret")
    assert "do-not-log-this-secret" not in window.workflow_log.toPlainText()

    window.set_translation_configuration(
        ProviderConfiguration(
            provider="ollama",
            base_url="http://localhost:11434",
            model="configured-ollama",
        )
    )
    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)
    assert constructed[-1] == ("ollama", None)


def test_codex_cli_provider_is_constructed_without_secret_or_model_override(
    qapp, monkeypatch
) -> None:
    configuration = ProviderConfiguration(
        provider="codex-cli",
        base_url="local://codex",
        model="default",
    )
    constructed = []
    marker = object()
    monkeypatch.setattr(
        "app.ui.main_window.CodexCliTranslationProvider",
        lambda received: constructed.append(received) or marker,
    )
    window = MainWindow()
    window.set_translation_configuration(configuration)

    assert window._build_translation_provider() is marker
    assert constructed == [configuration]


def test_thai_preview_and_source_toggle_preserve_editable_overlays(qapp, tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = make_project(project_dir)
    page = project.pages[0]
    page.blocks[0].translated_text = "ไทย"
    window = MainWindow()
    window.set_project(project, project_dir)
    block_id = page.blocks[0].id
    window.image_viewer.select_block(block_id)
    window._thai_font_path = font_path()
    window.image_viewer.zoom_in()
    original_scale = window.image_viewer.transform().m11()

    assert window.refresh_thai_preview()
    wait_for_workflow(qapp, window)

    preview = project_dir / "previews" / f"preview-page-{page.id}.png"
    assert preview.is_file()
    assert window.image_viewer.selected_block_id == block_id
    assert window.image_viewer._page.id == page.id
    assert window.image_viewer.transform().m11() == original_scale
    assert window.preview_viewer._page.id == page.id
    assert window.preview_viewer.transform().m11() != 1
    assert window.preview_viewer._items == {}
    assert window.show_source_image()
    assert window.image_viewer.selected_block_id == block_id


def test_reclean_selected_block_archives_only_its_override(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    page.blocks.append(
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=65, y=5, width=20, height=20),
            reading_order=3,
        )
    )
    first, second, third = page.blocks
    cleanup_dir = tmp_path / "cleanups" / f"page-{page.id}"
    cleanup_dir.mkdir(parents=True)
    first_cleanup = cleanup_dir / f"block-{first.id}.png"
    second_cleanup = cleanup_dir / f"block-{second.id}.png"
    third_cleanup = cleanup_dir / f"block-{third.id}.png"
    first_cleanup.write_bytes(b"first")
    second_cleanup.write_bytes(b"second")
    third_cleanup.write_bytes(b"third")
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.set_selected_block_ids((first.id, third.id), primary_id=third.id)
    window._thai_font_path = font_path()
    starts = []
    monkeypatch.setattr(
        window,
        "_start_page_preview",
        lambda received, name: starts.append((received.id, name)) or True,
    )

    assert window.reclean_selected_block()

    assert not first_cleanup.exists()
    assert not third_cleanup.exists()
    assert first_cleanup.with_name(f"{first_cleanup.name}.bak").read_bytes() == b"first"
    assert third_cleanup.with_name(f"{third_cleanup.name}.bak").read_bytes() == b"third"
    assert second_cleanup.read_bytes() == b"second"
    assert starts == [(page.id, "Re-clean selected block")]
    assert "Archived existing cleanup file" in window.workflow_log.toPlainText()


def test_reclean_current_page_archives_page_overrides_and_rerenders_without_one(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    cleanup_dir = tmp_path / "cleanups" / f"page-{page.id}"
    cleanup_dir.mkdir(parents=True)
    cleanup_paths = [cleanup_dir / f"block-{block.id}.png" for block in page.blocks]
    for index, path in enumerate(cleanup_paths):
        path.write_bytes(f"cleanup-{index}".encode())
    cleanup_paths[0].with_name(f"{cleanup_paths[0].name}.bak").write_bytes(b"older")
    window = MainWindow()
    window.set_project(project, tmp_path)
    window._thai_font_path = font_path()
    starts = []
    monkeypatch.setattr(
        window,
        "_start_page_preview",
        lambda received, name: starts.append((received.id, name)) or True,
    )

    assert window.reclean_current_page()
    assert not any(path.exists() for path in cleanup_paths)
    assert cleanup_paths[0].with_name(f"{cleanup_paths[0].name}.bak2").is_file()
    assert cleanup_paths[1].with_name(f"{cleanup_paths[1].name}.bak").is_file()
    assert window.reclean_current_page()

    assert starts == [
        (page.id, "Re-clean current page"),
        (page.id, "Re-clean current page"),
    ]
    assert "no existing cleanup file" in window.workflow_log.toPlainText()


def test_reclean_actions_require_project_directory_page_and_selection(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    assert not window.reclean_selected_action.isEnabled()
    assert not window.reclean_page_action.isEnabled()
    assert not window.prepare_manual_cleanup_action.isEnabled()
    assert not window.prepare_page_cleanups_action.isEnabled()

    project = make_project(tmp_path)
    window.set_project(project)
    assert not window.reclean_selected_action.isEnabled()
    assert not window.reclean_page_action.isEnabled()
    assert not window.prepare_manual_cleanup_action.isEnabled()
    assert not window.prepare_page_cleanups_action.isEnabled()

    window.set_project(project, tmp_path)
    assert not window.reclean_selected_action.isEnabled()
    assert window.reclean_page_action.isEnabled()
    assert not window.prepare_manual_cleanup_action.isEnabled()
    assert not window.prepare_page_cleanups_action.isEnabled()

    window.image_viewer.select_block(project.pages[0].blocks[0].id)
    assert window.reclean_selected_action.isEnabled()
    assert window.prepare_manual_cleanup_action.isEnabled()

    project.pages[0].blocks[0].translated_text = "ไทย"
    window._update_action_states()
    assert window.prepare_page_cleanups_action.isEnabled()


def test_prepare_manual_cleanup_selected_block_opens_exact_crop(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    opened = []
    monkeypatch.setattr(
        "app.ui.main_window.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )

    assert window.prepare_manual_cleanup_selected_block()

    expected = ExportService._manual_cleanup_path(tmp_path, page, block)
    assert expected.is_file()
    assert [url.toLocalFile() for url in opened] == [str(expected)]
    assert "without changing the image size" in window.statusBar().currentMessage()


def test_prepare_manual_cleanups_current_page_targets_translated_blocks(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    page.blocks[0].translated_text = "หนึ่ง"
    window = MainWindow()
    window.set_project(project, tmp_path)
    opened = []
    monkeypatch.setattr(
        "app.ui.main_window.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )

    assert window.prepare_manual_cleanups_current_page()

    cleanup_path = ExportService._manual_cleanup_path(tmp_path, page, page.blocks[0])
    assert cleanup_path.is_file()
    assert not ExportService._manual_cleanup_path(tmp_path, page, page.blocks[1]).exists()
    assert [url.toLocalFile() for url in opened] == [str(cleanup_path.parent)]


def test_translation_completion_refreshes_preview_once_after_worker_is_idle(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    page = project.pages[0]
    block = page.blocks[0]
    block.status = BlockStatus.OCR_REVIEWED
    project.settings.default_source_language = SourceLanguage.EN

    class DeterministicProvider:
        async def translate_blocks(self, blocks, context):
            return [
                TranslationResult(id=item.id, translated_text=f"translated:{item.source_text}")
                for item in blocks
            ]

    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: DeterministicProvider(),
    )
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    window._thai_font_path = font_path()
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="ollama",
            base_url="http://localhost:11434",
            model="test-model",
        )
    )
    refresh_calls = []
    refresh = window.refresh_thai_preview

    def refresh_once():
        refresh_calls.append((window._current_page_id, window.is_busy))
        return refresh()

    monkeypatch.setattr(window, "refresh_thai_preview", refresh_once)

    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)

    preview = tmp_path / "previews" / f"preview-page-{page.id}.png"
    assert refresh_calls == [(page.id, False)]
    assert preview.is_file()
    assert window.preview_viewer._page.id == page.id
    assert window.workflow_log.toPlainText().count("Thai Preview started.") == 1


def test_translation_failure_cancellation_and_ocr_do_not_refresh_preview(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    block = project.pages[0].blocks[0]
    block.status = BlockStatus.OCR_REVIEWED
    project.pages[0].blocks[1].translated_text = "existing translation"
    project.settings.default_source_language = SourceLanguage.EN

    class FailingProvider:
        async def translate_blocks(self, blocks, context):
            raise RuntimeError("translation failed")

    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: FailingProvider(),
    )
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.image_viewer.select_block(block.id)
    window._thai_font_path = font_path()
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="ollama",
            base_url="http://localhost:11434",
            model="test-model",
        )
    )
    refresh_calls = []
    monkeypatch.setattr(
        window,
        "refresh_thai_preview",
        lambda: refresh_calls.append(True) or True,
    )

    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)
    assert "Translation issue: translation failed" in window.workflow_log.toPlainText()
    assert refresh_calls == []

    class SlowProvider:
        async def translate_blocks(self, blocks, context):
            await asyncio.sleep(0.05)
            return [TranslationResult(id=item.id, translated_text="translated") for item in blocks]

    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: SlowProvider(),
    )
    assert window.translate_selected_block()
    window.cancel_workflow()
    wait_for_workflow(qapp, window)
    assert "cancelled" in window.workflow_log.toPlainText()
    assert refresh_calls == []

    assert window.ocr_selected_block()
    wait_for_workflow(qapp, window)
    assert refresh_calls == []


def test_open_image_folder_uses_current_language_and_hidden_workspace(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    image_dir = tmp_path / "images"
    workspace = image_dir / ".manga-thai-translator"
    project = make_project(tmp_path)
    calls = []

    def open_folder(path, **kwargs):
        calls.append((Path(path), kwargs))
        return project, workspace

    monkeypatch.setattr("app.ui.main_window.ProjectService.open_image_folder", open_folder)
    window = MainWindow()
    window.project_language_combo.setCurrentIndex(
        window.project_language_combo.findData(SourceLanguage.AUTO.value)
    )

    assert window.open_image_folder(image_dir)

    assert window.project is project
    assert window._project_dir == workspace
    assert calls == [
        (
            image_dir,
            {
                "default_source_language": SourceLanguage.AUTO,
                "default_reading_order": ReadingOrderPreset.WEBTOON_VERTICAL,
            },
        )
    ]


def test_cancelled_open_images_picker_is_a_no_op(qapp, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getOpenFileNames",
        lambda *args, **kwargs: ([], "Images"),
    )
    monkeypatch.setattr(
        "app.ui.main_window.ProjectService.open_image_files",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    window = MainWindow()

    window._choose_image_files()

    assert calls == []
    assert window.project is None
    assert window._project_dir is None


def test_open_image_files_fresh_launch_uses_service_without_provider(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "page.png"
    save_image(image)
    provider_calls = []
    monkeypatch.setattr(
        MainWindow,
        "_build_translation_provider",
        lambda self: provider_calls.append(True),
    )
    window = MainWindow()
    window.project_language_combo.setCurrentIndex(
        window.project_language_combo.findData(SourceLanguage.KO.value)
    )

    assert window.open_image_files([image])

    workspace = tmp_path / ".manga-thai-translator"
    assert window._project_dir == workspace
    assert window.project.settings.default_source_language is SourceLanguage.KO
    assert window.project.settings.default_reading_order is ReadingOrderPreset.WEBTOON_VERTICAL
    assert window.image_viewer._page.id == window.project.pages[0].id
    assert Path(window.image_viewer._page.source_path) == image.resolve()
    assert provider_calls == []


def test_open_image_files_failure_preserves_current_project(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project)

    assert not window.open_image_files([tmp_path / "missing.png"])

    assert window.project is project
    assert "Could not open images" in window.statusBar().currentMessage()


def test_page_selection_loads_existing_preview_or_clears_it(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    preview = preview_dir / f"preview-page-{project.pages[0].id}.png"
    save_image(preview)
    window = MainWindow()

    window.set_project(project, tmp_path)
    assert window.preview_viewer._page.id == project.pages[0].id
    assert window.preview_viewer._items == {}
    window._zoom_in_viewers()
    assert window.image_viewer.transform().m11() > 1
    assert window.preview_viewer.transform().m11() > 1

    window.select_next_page()
    assert window.preview_viewer._page is None


def test_page_selection_auto_fits_long_original_and_preview(qapp, tmp_path: Path) -> None:
    source = tmp_path / "long.png"
    image = QImage(800, 3000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=800, height=3000)
    project = Project(name="long", pages=[page])
    previews = tmp_path / "previews"
    previews.mkdir()
    preview = previews / f"preview-page-{page.id}.png"
    assert image.save(str(preview))
    window = MainWindow()
    window.resize(900, 600)
    window.show()
    qapp.processEvents()

    window.set_project(project, tmp_path)
    qapp.processEvents()

    assert window.image_viewer.transform().m11() < 1
    assert window.preview_viewer.transform().m11() < 1


def test_original_and_preview_sync_zoom_and_scroll_both_directions(qapp, tmp_path: Path) -> None:
    source = tmp_path / "sync.png"
    image = QImage(800, 3000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=800, height=3000)
    project = Project(name="sync", pages=[page])
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    assert image.save(str(preview_dir / f"preview-page-{page.id}.png"))
    window = MainWindow()
    window.resize(1000, 600)
    window.show()
    window.set_project(project, tmp_path)
    window.image_viewer.reset_zoom()
    qapp.processEvents()

    assert window.preview_viewer.transform().m11() == pytest.approx(
        window.image_viewer.transform().m11()
    )
    original_scroll = window.image_viewer.verticalScrollBar()
    preview_scroll = window.preview_viewer.verticalScrollBar()
    initial_preview_center = window.preview_viewer.mapToScene(
        window.preview_viewer.viewport().rect().center()
    )
    original_scroll.setValue(original_scroll.maximum() // 2)
    qapp.processEvents()
    assert preview_scroll.value() / preview_scroll.maximum() == pytest.approx(
        original_scroll.value() / original_scroll.maximum(), abs=0.01
    )
    original_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())
    preview_center = window.preview_viewer.mapToScene(
        window.preview_viewer.viewport().rect().center()
    )
    assert preview_center.y() != pytest.approx(initial_preview_center.y(), abs=5)
    assert preview_center.y() == pytest.approx(original_center.y(), abs=2)

    preview_scroll.setValue(preview_scroll.maximum() * 3 // 4)
    window.preview_viewer.zoom_in()
    qapp.processEvents()

    assert window.image_viewer.transform().m11() == pytest.approx(
        window.preview_viewer.transform().m11()
    )
    assert original_scroll.value() / original_scroll.maximum() == pytest.approx(
        preview_scroll.value() / preview_scroll.maximum(), abs=0.01
    )
    original_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())
    preview_center = window.preview_viewer.mapToScene(
        window.preview_viewer.viewport().rect().center()
    )
    assert preview_center.y() == pytest.approx(original_center.y(), abs=2)


def test_delete_block_preserves_zoom_and_scroll(qapp, tmp_path: Path) -> None:
    source = tmp_path / "delete-view.png"
    image = QImage(800, 3000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    page = Page(source_path=str(source), width=800, height=3000)
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=100, y=1400, width=200, height=100),
        reading_order=1,
    )
    page.blocks = [block]
    window = MainWindow()
    window.resize(1000, 600)
    window.show()
    window.set_project(Project(name="delete-view", pages=[page]), tmp_path)
    window.image_viewer.reset_zoom()
    window.image_viewer.select_block(block.id)
    window.image_viewer.zoom_in()
    qapp.processEvents()
    before_scale = window.image_viewer.transform().m11()
    before_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())

    window._delete_selected_block()
    qapp.processEvents()

    after_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())
    assert window.image_viewer.transform().m11() == pytest.approx(before_scale)
    assert after_center.x() == pytest.approx(before_center.x(), abs=2)
    assert after_center.y() == pytest.approx(before_center.y(), abs=2)


def test_preview_refresh_preserves_zoom_and_scroll(qapp, tmp_path: Path) -> None:
    source = tmp_path / "refresh-view.png"
    preview = tmp_path / "preview.png"
    image = QImage(800, 3000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assert image.save(str(source))
    assert image.save(str(preview))
    page = Page(source_path=str(source), width=800, height=3000)
    window = MainWindow()
    window.resize(1000, 600)
    window.show()
    window.set_project(Project(name="refresh-view", pages=[page]), tmp_path)
    window.image_viewer.reset_zoom()
    window.image_viewer.zoom_in()
    window.image_viewer.verticalScrollBar().setValue(
        window.image_viewer.verticalScrollBar().maximum() * 2 // 3
    )
    qapp.processEvents()
    before_scale = window.image_viewer.transform().m11()
    before_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())
    before_scroll_ratio = (
        window.image_viewer.verticalScrollBar().value()
        / window.image_viewer.verticalScrollBar().maximum()
    )

    window._preview_completed((page.id, preview, (), ()))
    qapp.processEvents()

    original_center = window.image_viewer.mapToScene(window.image_viewer.viewport().rect().center())
    preview_center = window.preview_viewer.mapToScene(
        window.preview_viewer.viewport().rect().center()
    )
    assert window.image_viewer.transform().m11() == pytest.approx(before_scale)
    assert original_center.x() == pytest.approx(before_center.x(), abs=10)
    assert original_center.y() == pytest.approx(before_center.y(), abs=10)
    assert (
        window.image_viewer.verticalScrollBar().value()
        / window.image_viewer.verticalScrollBar().maximum()
    ) == pytest.approx(before_scroll_ratio, abs=0.01)
    assert window.preview_viewer.transform().m11() == pytest.approx(before_scale)
    assert preview_center.x() == pytest.approx(original_center.x(), abs=2)
    assert preview_center.y() == pytest.approx(original_center.y(), abs=2)


def test_translate_folder_images_runs_in_background_persists_and_shows_preview(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    provider = object()
    created = []

    class Service:
        def __init__(self, received_provider, received_font):
            created.append((received_provider, Path(received_font)))

        async def run(self, project, project_dir, **kwargs):
            preview_dir = Path(project_dir) / "previews"
            preview_dir.mkdir(exist_ok=True)
            page = project.pages[0]
            (preview_dir / f"preview-page-{page.id}.png").write_bytes(
                Path(page.source_path).read_bytes()
            )
            return WorkflowResult(project=project)

    monkeypatch.setattr("app.ui.main_window.FolderTranslationService", Service)
    monkeypatch.setattr(
        "app.ui.main_window.CodexCliTranslationProvider", lambda configuration: provider
    )
    window = MainWindow()
    window.set_project(project, tmp_path)
    window._thai_font_path = font_path()
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="codex-cli",
            model="default",
            uploads_images=False,
        )
    )
    assert not window.translate_folder_action.isEnabled()
    assert not window.translate_folder_images()
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="codex-cli",
            model="default",
            uploads_images=True,
        )
    )

    assert window.translate_folder_action.isEnabled()
    assert window.translate_folder_images()
    wait_for_workflow(qapp, window)

    assert created == [(provider, font_path())]
    assert (tmp_path / "project.json").is_file()
    assert window.preview_viewer._page.id == project.pages[0].id
    assert "Folder image translation completed with 0 issues." in window.workflow_log.toPlainText()


def test_thai_font_selection_is_validated_visible_and_reused_for_export(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    window = MainWindow()
    window.set_project(project, tmp_path)
    selected_font = font_path()
    invalid_font = tmp_path / "font.txt"
    invalid_font.write_text("not a font")
    font_dialogs = []
    choices = iter((invalid_font, selected_font))

    def choose_font(*args, **kwargs):
        font_dialogs.append(True)
        return str(next(choices)), "Fonts"

    monkeypatch.setattr("app.ui.main_window.QFileDialog.getOpenFileName", choose_font)
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *args, **kwargs: str(tmp_path / "export"),
    )
    item_answers = iter((("None", True), ("None", True)))
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getItem",
        lambda *args, **kwargs: next(item_answers),
    )
    calls = []
    monkeypatch.setattr(
        window,
        "start_export",
        lambda output, font, **options: calls.append((Path(output), Path(font), options)) or True,
    )

    assert not window._choose_thai_font()
    assert window._thai_font_path is None
    assert window._choose_thai_font()
    window._choose_export()

    assert font_dialogs == [True, True]
    assert selected_font.name in window.choose_thai_font_action.toolTip()
    assert calls == [
        (
            tmp_path / "export",
            selected_font,
            {
                "watermark_text": None,
                "watermark_logo_path": None,
                "banner_path": None,
                "banner_position": "end",
            },
        )
    ]
    assert selected_font.name not in project.model_dump_json()


def test_translation_selected_page_all_scopes_and_review_gate(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    first_page = project.pages[0]
    gated, reviewed = first_page.blocks
    gated.status = BlockStatus.OCR_COMPLETE
    reviewed.status = BlockStatus.OCR_REVIEWED
    second_page = project.pages[1]
    other = TextBlock(
        page_id=second_page.id,
        bbox=BoundingBox(x=5, y=5, width=20, height=20),
        reading_order=1,
        source_text="other",
        status=BlockStatus.OCR_REVIEWED,
    )
    second_page.blocks = [other]
    project.settings.default_source_language = SourceLanguage.EN
    calls = []

    class DeterministicProvider:
        async def translate_blocks(self, blocks, context):
            calls.append(tuple(item.id for item in blocks))
            return [
                TranslationResult(id=item.id, translated_text=f"translated:{item.source_text}")
                for item in blocks
            ]

    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: DeterministicProvider(),
    )
    window = MainWindow()
    window.set_project(project)
    window.set_translation_configuration(
        ProviderConfiguration(
            provider="ollama",
            base_url="http://localhost:11434",
            model="test-model",
        )
    )
    window.image_viewer.select_block(gated.id)

    assert window.translate_selected_block()
    wait_for_workflow(qapp, window)
    assert calls == []
    assert "OCR review is required" in window.workflow_log.toPlainText()

    assert window.translate_current_page()
    wait_for_workflow(qapp, window)
    assert calls[-1] == (reviewed.id,)
    blocks = {block.id: block for page in window.project.pages for block in page.blocks}
    assert blocks[reviewed.id].status is BlockStatus.TRANSLATED
    assert blocks[other.id].status is BlockStatus.OCR_REVIEWED

    assert window.translate_all_pages()
    wait_for_workflow(qapp, window)
    assert other.id in calls[-1]
    blocks = {block.id: block for page in window.project.pages for block in page.blocks}
    assert blocks[other.id].status is BlockStatus.TRANSLATED


def test_workflow_issues_are_visible_and_current_page_is_preserved(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first.png"
    save_image(first)
    project = Project(
        name="issues",
        pages=[
            Page(source_path=str(first), width=120, height=100),
            Page(source_path=str(tmp_path / "missing.png"), width=120, height=100),
        ],
    )
    project.settings.default_source_language = SourceLanguage.EN
    window = MainWindow()
    window.set_project(project)
    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: DeterministicTranslationProvider(),
    )
    window.set_translation_configuration(
        ProviderConfiguration(provider="ollama", model="test", base_url="http://localhost")
    )
    window.page_sidebar.select_page(project.pages[1].id)

    assert window.run_workflow()
    wait_for_workflow(qapp, window)

    assert window.page_sidebar.currentItem().data(Qt.ItemDataRole.UserRole) == project.pages[1].id
    assert "Detection issue: image not found" in window.workflow_log.toPlainText()
    assert "Workflow completed with 1 issue." in window.progress_label.text()
    assert not window.progress_dock.isHidden()


def test_workflow_issue_format_includes_visible_numbers_and_unknown_ids(
    qapp, tmp_path: Path
) -> None:
    project = make_project(tmp_path)
    block = TextBlock(
        page_id=project.pages[1].id,
        bbox=BoundingBox(x=5, y=5, width=20, height=20),
        reading_order=7,
    )
    project.pages[1].blocks = [block]
    window = MainWindow()
    window.set_project(project)

    known = WorkflowIssue(
        stage="translation",
        message="review required",
        page_id=project.pages[1].id,
        block_id=block.id,
        recoverable=True,
    )
    unknown_page_id = uuid4()
    unknown_block_id = uuid4()
    unknown = known.model_copy(update={"page_id": unknown_page_id, "block_id": unknown_block_id})

    assert window._format_issue(known) == (
        "Translation issue: review required "
        f"(Page 2, Block 7, page_id={project.pages[1].id}, block_id={block.id})"
    )
    assert window._format_issue(unknown) == (
        f"Translation issue: review required (page_id={unknown_page_id}, block_id={unknown_block_id})"
    )


def test_export_and_preview_logs_include_visible_numbers_and_uuids(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    page = project.pages[1]
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=5, y=5, width=20, height=20),
        reading_order=7,
    )
    page.blocks = [block]
    window = MainWindow()
    window.set_project(project)
    issue = ExportIssue(message="cleanup required", page_id=page.id, block_id=block.id)
    warning = OverflowWarning(message="text overflow", page_id=page.id, block_id=block.id)
    issue_message = (
        f"Export issue: cleanup required (Page 2, Block 7, page_id={page.id}, block_id={block.id})"
    )
    warning_message = (
        f"Overflow warning: text overflow (Page 2, Block 7, page_id={page.id}, block_id={block.id})"
    )

    window._export_completed(
        ExportResult(
            json_path=tmp_path / "export.json",
            csv_path=tmp_path / "export.csv",
            txt_path=tmp_path / "export.txt",
            issues=(issue,),
            overflow_warnings=(warning,),
        )
    )
    export_log = window.workflow_log.toPlainText()
    assert issue_message in export_log
    assert warning_message in export_log

    window.workflow_log.clear()
    window._preview_completed((page.id, tmp_path / "preview.png", (warning,), (issue,)))
    preview_log = window.workflow_log.toPlainText()
    assert issue_message in preview_log
    assert warning_message in preview_log


def test_cancel_and_close_wait_for_worker_without_mutating_project(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "slow.png"
    save_image(image)
    project = Project(
        name="slow",
        pages=[Page(source_path=str(image), width=120, height=100)],
    )
    project.settings.default_source_language = SourceLanguage.JA

    class SlowDetector:
        async def detect(self, image):
            await asyncio.sleep(0.05)
            return []

    monkeypatch.setattr("app.ui.main_window.MockTextDetectionProvider", SlowDetector)
    monkeypatch.setattr(
        "app.ui.main_window.OllamaTranslationProvider",
        lambda configuration: DeterministicTranslationProvider(),
    )
    window = MainWindow()
    window.set_project(project)
    window.set_translation_configuration(
        ProviderConfiguration(provider="ollama", model="test", base_url="http://localhost")
    )
    original = project.model_dump_json()

    assert window.run_workflow()
    worker = window._workflow_worker
    assert worker is not None
    window.cancel_workflow()
    assert worker._cancellation.is_cancelled
    wait_for_workflow(qapp, window)
    assert window.project is project
    assert project.model_dump_json() == original
    assert "cancelled" in window.workflow_log.toPlainText()

    assert window.run_workflow()
    thread = window._workflow_thread
    assert thread is not None
    window.close()
    assert not thread.isRunning()


def test_close_ignores_timeout_and_keeps_worker_references(qapp) -> None:
    class FakeWorker:
        def __init__(self):
            self.cancelled = False

        def cancel(self):
            self.cancelled = True

    class FakeThread:
        def __init__(self):
            self.quit_called = False
            self.wait_timeout = None

        def quit(self):
            self.quit_called = True

        def wait(self, timeout):
            self.wait_timeout = timeout
            return False

    class FakeCloseEvent:
        def __init__(self):
            self.accepted = False
            self.ignored = False

        def accept(self):
            self.accepted = True

        def ignore(self):
            self.ignored = True

    window = MainWindow()
    worker = FakeWorker()
    thread = FakeThread()
    event = FakeCloseEvent()
    window._workflow_worker = worker
    window._workflow_thread = thread

    window.closeEvent(event)

    assert worker.cancelled
    assert thread.quit_called
    assert thread.wait_timeout == 2_000
    assert event.ignored
    assert not event.accepted
    assert window._workflow_worker is worker
    assert window._workflow_thread is thread
    assert "Workflow is still stopping" in window.statusBar().currentMessage()
    assert "Workflow is still stopping" in window.workflow_log.toPlainText()
    window._workflow_worker = None
    window._workflow_thread = None


def test_main_reuses_existing_application_without_entering_event_loop(qapp) -> None:
    assert main([]) == 0


def test_page_management_actions_and_reorder_keep_page_identity(qapp, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first, second = project.pages
    window = MainWindow()
    window.set_project(project, tmp_path)

    navigate_action = next(
        action for action in window.menuBar().actions() if action.text() == "Navigate"
    )
    navigate_menu = navigate_action.menu()
    assert {
        "Move Page Earlier",
        "Move Page Later",
        "Delete Current Page",
    } <= {action.text() for action in navigate_menu.actions()}
    assert window.move_page_earlier_action.shortcut().toString()
    assert window.move_page_later_action.shortcut().toString()
    assert window.move_page_earlier_action.shortcut() not in {
        window.previous_page_action.shortcut(),
        window.next_page_action.shortcut(),
        window.move_block_earlier_action.shortcut(),
        window.move_block_later_action.shortcut(),
    }

    assert not window.move_page_earlier_action.isEnabled()
    assert window.move_page_later_action.isEnabled()
    assert window.delete_page_action.isEnabled()
    assert window.move_page_earlier() is False
    assert [page.id for page in project.pages] == [first.id, second.id]

    window.page_sidebar.select_page(second.id)
    assert window._current_page_id == second.id
    assert window.move_page_earlier() is True
    assert project.pages[0] is second
    assert project.pages[1] is first
    assert window._current_page_id == second.id
    assert window.page_sidebar.currentRow() == 0
    assert window.page_sidebar.item(0).text().startswith("1. ")
    assert window.page_sidebar.item(1).text().startswith("2. ")
    assert not window.move_page_earlier_action.isEnabled()
    assert window.move_page_later_action.isEnabled()

    assert window.move_page_later() is True
    assert project.pages[0] is first
    assert project.pages[1] is second
    assert window._current_page_id == second.id
    assert window.page_sidebar.currentRow() == 1
    assert window.move_page_later() is False


def test_delete_current_page_cancel_preserves_project_and_source_image(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    second = project.pages[1]
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.page_sidebar.select_page(second.id)
    before = list(project.pages)
    prompts = []
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *args: prompts.append(args) or QMessageBox.StandardButton.No,
    )

    assert window.delete_current_page() is False

    assert project.pages == before
    assert window._current_page_id == second.id
    assert Path(second.source_path).is_file()
    assert len(prompts) == 1
    prompt = prompts[0][2].lower()
    assert "project" in prompt
    assert "source image" in prompt
    assert "not be deleted" in prompt


def test_delete_current_page_keeps_row_selection_and_round_trips_order(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    first, second = project.pages
    third_path = tmp_path / "หน้า3.png"
    save_image(third_path)
    third = Page(source_path=str(third_path), width=120, height=100)
    project.pages.append(third)
    window = MainWindow()
    window.set_project(project, tmp_path)
    window.page_sidebar.select_page(second.id)
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    assert window.delete_current_page() is True

    assert project.pages == [first, third]
    assert project.pages[0] is first
    assert project.pages[1] is third
    assert window._current_page_id == third.id
    assert window.page_sidebar.currentRow() == 1
    assert window.delete_page_action.isEnabled()
    assert not window.move_page_later_action.isEnabled()
    assert third_path.is_file()

    assert window.move_page_earlier()
    assert [page.id for page in project.pages] == [third.id, first.id]
    assert window.save_project()
    restored = ProjectRepository.load(tmp_path)
    assert [page.id for page in restored.pages] == [third.id, first.id]
    assert all(Path(page.source_path).is_file() for page in restored.pages)


def test_page_management_busy_boundaries_and_delete_last_page_clear_ui(
    qapp, tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    first, second = project.pages
    window = MainWindow()
    window.set_project(project, tmp_path)

    assert not window.move_page_earlier()
    window._workflow_thread = object()
    assert not window.move_page_later()
    assert not window.delete_current_page()
    assert project.pages == [first, second]
    window._workflow_thread = None
    window._update_action_states()

    window.page_sidebar.select_page(second.id)
    assert not window.move_page_later()
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    assert window.delete_current_page()
    assert project.pages == [first]
    assert window._current_page_id == first.id

    assert window.delete_current_page()
    assert project.pages == []
    assert window._current_page_id is None
    assert window._current_block_id is None
    assert window.page_sidebar.count() == 0
    assert window.image_viewer._page is None
    assert window.preview_viewer._page is None
    assert not window.block_strip._cards
    assert not window.block_editor.isEnabled()
    assert not window.previous_page_action.isEnabled()
    assert not window.next_page_action.isEnabled()
    assert not window.move_page_earlier_action.isEnabled()
    assert not window.move_page_later_action.isEnabled()
    assert not window.delete_page_action.isEnabled()
    assert not window.delete_current_page()
