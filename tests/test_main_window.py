import asyncio
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import ImageFont
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QSplitter

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
from app.services.export import ExportIssue, ExportResult, OverflowWarning
from app.services.text_detection import MockTextDetectionProvider
from app.services.workflow import WorkflowIssue, WorkflowResult
from app.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def clean_up_top_level_widgets(qapp):
    def clean_up() -> None:
        for widget in qapp.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

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
    assert window.splitter.widget(0) is window.page_sidebar
    assert window.splitter.widget(1) is window.comparison_pane
    assert window.splitter.widget(2) is window.block_editor
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
    window.image_viewer.block_created.emit(created)
    assert page.blocks[-1] is created
    assert window.page_sidebar.item(0).text().endswith("3 blocks")

    moved = created.model_copy(update={"bbox": BoundingBox(x=70, y=10, width=25, height=25)})
    window.image_viewer.block_changed.emit(moved)
    assert page.blocks[-1].id == created.id
    assert page.blocks[-1].bbox == moved.bbox

    window.image_viewer.select_block(first.id)
    window.select_next_block()
    assert window.image_viewer.selected_block_id == second.id
    window.select_previous_block()
    assert window.image_viewer.selected_block_id == first.id

    window.image_viewer.select_block(created.id)
    window.image_viewer.delete_selected_block()
    assert all(block.id != created.id for block in page.blocks)

    window.block_editor.set_block(edited)
    window.block_editor.delete_requested.emit(edited.id)
    assert all(block.id != edited.id for block in page.blocks)
    assert not window.block_editor.isEnabled()
    assert window.page_sidebar.item(0).text().endswith("1 blocks")


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
    )
    shortcuts = [action.shortcut().toString() for action in actions]

    assert all(shortcuts)
    assert len(shortcuts) == len(set(shortcuts))
    assert all(action in window.toolbar.actions() for action in actions)
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
        window.translate_selected_action,
        window.translate_page_action,
        window.translate_all_action,
    )
    assert all(action.shortcut().isEmpty() for action in scoped_actions)
    assert len({action.text() for action in scoped_actions}) == len(scoped_actions)
    assert window.open_images_action.shortcut().toString() == "Ctrl+O"
    assert window.open_image_folder_action.shortcut().toString() == "Ctrl+Shift+O"
    assert window.toolbar.actions()[0] is window.open_images_action


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
    assert "Confirmed OCR for 2 block(s)" in window.statusBar().currentMessage()
    unchanged = project.model_dump_json()
    assert not window.confirm_ocr_current_page()
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
    assert "1 issue(s)" in window.progress_label.text()
    assert "1 overflow warning(s)" in window.progress_label.text()
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
    monkeypatch.setattr(
        window,
        "start_export",
        lambda output, font, background_color="white": (
            calls.append((Path(output), Path(font), background_color)) or True
        ),
    )

    window._choose_export()

    assert calls == [(tmp_path / "dialog-output", selected_font, "white")]


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

    assert "Export cancelled" in window.workflow_log.toPlainText()
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
    assert window.project is project
    wait_for_workflow(qapp, window)

    assert window.project is not project
    block = window.project.pages[0].blocks[0]
    assert block.status is BlockStatus.OCR_COMPLETE
    assert block.source_text == "おかえり"
    assert block.translated_text == ""
    assert "Ocr 1/1" in window.workflow_log.toPlainText()
    assert "Translation 1/1" not in window.workflow_log.toPlainText()
    assert "completed with 0 issue" in window.progress_label.text()
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
    assert window.workflow_log.toPlainText().count("Thai preview started.") == 1


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
    assert "cancelled" in window.workflow_log.toPlainText().lower()
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
    assert "completed with 0 issue" in window.workflow_log.toPlainText()


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
    calls = []
    monkeypatch.setattr(
        window,
        "start_export",
        lambda output, font: calls.append((Path(output), Path(font))) or True,
    )

    assert not window._choose_thai_font()
    assert window._thai_font_path is None
    assert window._choose_thai_font()
    window._choose_export()

    assert font_dialogs == [True, True]
    assert selected_font.name in window.choose_thai_font_action.toolTip()
    assert calls == [(tmp_path / "export", selected_font)]
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
    assert "completed with 1 issue" in window.progress_label.text()
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
        "Translation issue: review required "
        f"(page_id={unknown_page_id}, block_id={unknown_block_id})"
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
    assert "cancelled" in window.workflow_log.toPlainText().lower()

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
    assert "still stopping" in window.statusBar().currentMessage()
    assert "still stopping" in window.workflow_log.toPlainText()
    window._workflow_worker = None
    window._workflow_thread = None


def test_main_reuses_existing_application_without_entering_event_loop(qapp) -> None:
    assert main([]) == 0
