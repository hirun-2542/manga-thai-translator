import asyncio
import threading
import time
from pathlib import Path

from PIL import ImageFont
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QDialog, QSplitter

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
from app.ui.main_window import MainWindow


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


def test_layout_set_project_and_page_navigation(qapp, tmp_path: Path) -> None:
    window = MainWindow()
    project = make_project(tmp_path)

    window.set_project(project)

    assert isinstance(window.centralWidget(), QSplitter)
    assert window.splitter.widget(0) is window.page_sidebar
    assert window.splitter.widget(1) is window.image_viewer
    assert window.splitter.widget(2) is window.block_editor
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
        window.new_project_action,
        window.open_action,
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
        window.configure_translation_action,
        window.export_action,
        window.ocr_selected_action,
        window.ocr_page_action,
        window.ocr_all_action,
        window.translate_selected_action,
        window.translate_page_action,
        window.translate_all_action,
    )
    assert all(action.shortcut().isEmpty() for action in scoped_actions)
    assert len({action.text() for action in scoped_actions}) == len(scoped_actions)


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
    window._configure_translation_provider()

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


def test_installed_ocr_router_is_explicit_and_lazy(qapp) -> None:
    window = MainWindow()
    window.ocr_mode_combo.setCurrentIndex(window.ocr_mode_combo.findData("installed-local"))

    router = window._build_ocr_provider()

    assert router.provider_name_for(SourceLanguage.JA) == "manga-ocr"
    for language in (
        SourceLanguage.EN,
        SourceLanguage.KO,
        SourceLanguage.ZH_HANS,
        SourceLanguage.ZH_HANT,
    ):
        assert router.provider_name_for(language) == "paddleocr"


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


def test_mock_workflow_updates_project_progress_and_not_saved_file(qapp, tmp_path: Path) -> None:
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

    assert window.run_mock_workflow()
    assert window.is_busy
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
    assert block.status is BlockStatus.TRANSLATED
    assert block.translated_text == "แปลไทย: おかえり"
    assert "Translation 1/1" in window.workflow_log.toPlainText()
    assert "completed with 0 issue" in window.progress_label.text()
    assert (project_dir / "project.json").read_bytes() == saved
    assert window.run_workflow_action.isEnabled()
    assert not window.cancel_workflow_action.isEnabled()
    assert window.splitter.isEnabled()
    assert window.page_sidebar.isEnabled()
    assert window.image_viewer.isEnabled()
    assert window.project_language_combo.isEnabled()
    assert window.page_language_combo.isEnabled()
    assert window.ocr_mode_combo.isEnabled()


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


def test_workflow_issues_are_visible_and_current_page_is_preserved(qapp, tmp_path: Path) -> None:
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
    window.page_sidebar.select_page(project.pages[1].id)

    assert window.run_mock_workflow()
    wait_for_workflow(qapp, window)

    assert window.page_sidebar.currentItem().data(Qt.ItemDataRole.UserRole) == project.pages[1].id
    assert "Detection issue: image not found" in window.workflow_log.toPlainText()
    assert "completed with 1 issue" in window.progress_label.text()
    assert not window.progress_dock.isHidden()


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
    window = MainWindow()
    window.set_project(project)
    original = project.model_dump_json()

    assert window.run_mock_workflow()
    worker = window._workflow_worker
    assert worker is not None
    window.cancel_workflow()
    assert worker._cancellation.is_cancelled
    wait_for_workflow(qapp, window)
    assert window.project is project
    assert project.model_dump_json() == original
    assert "cancelled" in window.workflow_log.toPlainText().lower()

    assert window.run_mock_workflow()
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
