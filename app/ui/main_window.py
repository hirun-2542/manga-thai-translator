"""Main window coordinating project models and offline workflows."""

from collections.abc import Callable, Iterable
from pathlib import Path
from uuid import UUID

from PIL import ImageFont
from PySide6.QtCore import QSignalBlocker, Qt, QThread
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.core.language import resolve_source_language
from app.core.models import (
    BlockStatus,
    Page,
    Project,
    ProviderConfiguration,
    ReadingOrderPreset,
    SourceLanguage,
    TextBlock,
    utc_now,
)
from app.core.reading_order import normalize_reading_order
from app.persistence.project_repository import ProjectRepository
from app.persistence.secrets import resolve_api_key
from app.services.export import ExportIssue, ExportProgress, ExportResult
from app.services.folder_translation import FolderTranslationService
from app.services.ocr import (
    MangaOcrProvider,
    MockOcrProvider,
    OcrProviderRegistry,
    OcrRouter,
    PaddleOcrProvider,
)
from app.services.project_service import ProjectService
from app.services.text_detection import MockTextDetectionProvider
from app.services.translation import (
    CodexCliTranslationProvider,
    MockTranslationProvider,
    OllamaTranslationProvider,
    OpenAICompatibleTranslationProvider,
    TranslationProvider,
)
from app.services.workflow import (
    ProgressUpdate,
    WorkflowIssue,
    WorkflowMode,
    WorkflowResult,
    WorkflowService,
)
from app.ui.block_editor import BlockEditor
from app.ui.image_viewer import ImageViewer
from app.ui.page_sidebar import PageSidebar
from app.ui.provider_dialog import TranslationProviderDialog
from app.ui.workers import (
    ExportWorker,
    FolderTranslationWorker,
    PreviewWorker,
    WorkflowWorker,
)

_CLOSE_WAIT_MS = 2_000
_WORKFLOW_UNDO_LIMIT = 20


class MainWindow(QMainWindow):
    """Coordinate the persisted project model without owning domain workflows."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._project_dir: Path | None = None
        self._current_page_id: UUID | None = None
        self._current_block_id: UUID | None = None
        self._workflow_thread: QThread | None = None
        self._workflow_worker: (
            WorkflowWorker | FolderTranslationWorker | ExportWorker | PreviewWorker | None
        ) = None
        self._workflow_name = "Workflow"
        self._refresh_preview_after_translation = False
        self._translation_configuration: ProviderConfiguration | None = None
        self._thai_font_path: Path | None = None
        self._workflow_undo_history: list[tuple[Project, UUID | None, UUID | None]] = []

        self.page_sidebar = PageSidebar()
        self.image_viewer = ImageViewer()
        self.preview_viewer = ImageViewer()
        self.block_editor = BlockEditor()
        comparison = QSplitter()
        for label, viewer in (
            ("Original", self.image_viewer),
            ("Thai Preview", self.preview_viewer),
        ):
            pane = QWidget()
            layout = QVBoxLayout(pane)
            layout.addWidget(QLabel(label))
            layout.addWidget(viewer)
            comparison.addWidget(pane)
        splitter = QSplitter()
        splitter.addWidget(self.page_sidebar)
        splitter.addWidget(comparison)
        splitter.addWidget(self.block_editor)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self.splitter = splitter
        self.comparison_pane = comparison
        self._create_progress_dock()
        self._create_language_toolbar()
        self.statusBar().showMessage("Open an existing project to begin.")

        self._create_actions()
        self._connect_widgets()
        self.setWindowTitle("Manga Thai Translator")
        self.resize(1200, 760)

    @property
    def project(self) -> Project | None:
        return self._project

    @property
    def is_busy(self) -> bool:
        return self._workflow_thread is not None

    @property
    def translation_configuration(self) -> ProviderConfiguration | None:
        return self._translation_configuration

    def set_translation_configuration(self, configuration: ProviderConfiguration | None) -> None:
        self._translation_configuration = configuration
        if configuration is None:
            self._append_log("Translation provider configuration cleared.")
        else:
            self._append_log(
                f"Translation provider configured: {configuration.provider} / {configuration.model}"
            )
        self._update_action_states()

    def set_project(self, project: Project | None, project_dir: Path | str | None = None) -> None:
        directory = Path(project_dir) if project_dir is not None else None
        if (
            self._project is None
            or project is None
            or self._project.id != project.id
            or self._project_dir != directory
        ):
            self._workflow_undo_history.clear()
        self._project = project
        self._project_dir = directory
        self._current_page_id = None
        self._current_block_id = None
        self.block_editor.set_block(None)
        self.image_viewer.set_page(None)
        self.preview_viewer.set_page(None)
        self.page_sidebar.set_pages(project.pages if project is not None else [])
        self._sync_language_controls()

        if project is None:
            self.setWindowTitle("Manga Thai Translator")
            self.statusBar().showMessage("No project is open.")
            self._update_action_states()
            return

        self.setWindowTitle(f"{project.name} — Manga Thai Translator")
        if project.pages:
            self.page_sidebar.select_page(project.pages[0].id)
        else:
            self.statusBar().showMessage(f"Opened {project.name}: no pages.")
        self._update_action_states()

    def create_project(
        self,
        project_dir: Path | str,
        name: str,
        image_paths: Path | str | Iterable[Path | str],
        default_source_language: SourceLanguage = SourceLanguage.AUTO,
        default_reading_order: ReadingOrderPreset = ReadingOrderPreset.MANGA_RTL,
        copy_sources: bool = True,
    ) -> bool:
        """Create and display a project without invoking native dialogs."""
        name = name.strip()
        if not name:
            self._report_error("Could not create project: project name is required")
            return False
        try:
            project = ProjectService.create_project(
                project_dir,
                name,
                image_paths,
                default_source_language,
                default_reading_order,
                copy_sources,
            )
        except Exception as error:
            self._report_error(f"Could not create project: {error}")
            return False
        self.set_project(project, project_dir)
        return True

    def import_images(
        self,
        image_paths: Path | str | Iterable[Path | str],
        copy_sources: bool = True,
    ) -> bool:
        """Import images into the open project without invoking native dialogs."""
        if self._project is None or self._project_dir is None:
            self._report_error("Could not import images: no saved project is open")
            return False
        try:
            project = ProjectService.import_images(
                self._project,
                self._project_dir,
                image_paths,
                copy_sources,
            )
        except Exception as error:
            self._report_error(f"Could not import images: {error}")
            return False
        self._replace_project_preserving_page(project)
        return True

    def open_project(self, project_dir: Path | str) -> bool:
        try:
            project = ProjectRepository.load(project_dir)
        except Exception as error:
            self.statusBar().showMessage(f"Could not open project: {error}")
            return False
        self.set_project(project, project_dir)
        return True

    def open_image_folder(self, image_dir: Path | str) -> bool:
        try:
            project, project_dir = ProjectService.open_image_folder(
                image_dir,
                default_source_language=SourceLanguage(self.project_language_combo.currentData()),
                default_reading_order=ReadingOrderPreset.WEBTOON_VERTICAL,
            )
        except Exception as error:
            self._report_error(f"Could not open image folder: {error}")
            return False
        self.set_project(project, project_dir)
        return True

    def open_image_files(self, image_paths: Iterable[Path | str]) -> bool:
        try:
            project, project_dir = ProjectService.open_image_files(
                image_paths,
                default_source_language=SourceLanguage(self.project_language_combo.currentData()),
                default_reading_order=ReadingOrderPreset.WEBTOON_VERTICAL,
            )
        except Exception as error:
            self._report_error(f"Could not open images: {error}")
            return False
        self.set_project(project, project_dir)
        return True

    def save_project(self) -> bool:
        if self._project is None or self._project_dir is None:
            self.statusBar().showMessage("Choose an existing project directory before saving.")
            return False
        try:
            destination = ProjectRepository.save(self._project, self._project_dir)
        except Exception as error:
            self.statusBar().showMessage(f"Could not save project: {error}")
            return False
        self.statusBar().showMessage(f"Saved {destination}")
        return True

    def run_workflow(self) -> bool:
        configuration = self._translation_configuration
        if self._project is None or not self._project.pages or self.is_busy:
            return False
        if (
            configuration is not None
            and configuration.provider == "codex-cli"
            and configuration.uploads_images
        ):
            return self.translate_folder_images()
        return self._start_ocr(None, None, "Workflow")

    def undo_last_workflow(self) -> bool:
        if self.is_busy or not self._workflow_undo_history:
            return False
        project, page_id, block_id = self._workflow_undo_history[-1]
        if self._project_dir is not None:
            try:
                ProjectRepository.save(project, self._project_dir)
            except Exception as error:
                self._report_error(f"Could not undo workflow: {error}")
                return False
        self._workflow_undo_history.pop()
        self._clear_stale_previews(project)
        self._replace_project_with_selection(project, page_id, block_id)
        self.preview_viewer.set_page(None)
        message = "Undid last workflow."
        self.statusBar().showMessage(message)
        self._append_log(message)
        self._update_action_states()
        return True

    def _dispatch_undo(self) -> bool:
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                if not widget.isReadOnly():
                    widget.undo()
                    return True
                break
            widget = widget.parentWidget()
        return self.undo_last_workflow()

    def ocr_selected_block(self) -> bool:
        page = self._current_page()
        block = self._current_block()
        if page is None or block is None:
            return False
        return self._start_ocr((page.id,), (block.id,), "OCR selected block")

    def ocr_current_page(self) -> bool:
        page = self._current_page()
        if page is None:
            return False
        return self._start_ocr((page.id,), None, "OCR current page")

    def ocr_all_pages(self) -> bool:
        if self._project is None or not self._project.pages:
            return False
        return self._start_ocr(None, None, "OCR all pages")

    def confirm_ocr_current_page(self) -> bool:
        page = self._current_page()
        if page is None or self.is_busy:
            return False
        eligible = {block.id for block in page.blocks if block.status is BlockStatus.OCR_COMPLETE}
        if not eligible:
            return False
        updated_at = utc_now()
        page.blocks = [
            (
                block.model_copy(
                    update={"status": BlockStatus.OCR_REVIEWED, "updated_at": updated_at}
                )
                if block.id in eligible
                else block
            )
            for block in page.blocks
        ]
        selected = self._current_block()
        if selected is not None:
            self.image_viewer.update_block(selected)
            self.block_editor.set_block(selected)
        self._update_action_states()
        message = f"Confirmed OCR for {len(eligible)} block(s) on current page."
        self.statusBar().showMessage(message)
        self._append_log(message)
        return True

    def translate_selected_block(self) -> bool:
        page = self._current_page()
        block = self._current_block()
        if page is None or block is None:
            return False
        return self._start_translation((page.id,), (block.id,), "Translate selected block")

    def translate_current_page(self) -> bool:
        page = self._current_page()
        if page is None:
            return False
        return self._start_translation((page.id,), None, "Translate current page")

    def translate_all_pages(self) -> bool:
        if self._project is None or not self._project.pages:
            return False
        return self._start_translation(None, None, "Translate all pages")

    def translate_folder_images(self) -> bool:
        configuration = self._translation_configuration
        if (
            self._project is None
            or not self._project.pages
            or self._project_dir is None
            or self.is_busy
            or configuration is None
            or configuration.provider != "codex-cli"
            or not configuration.uploads_images
        ):
            return False
        if not self._ensure_thai_font():
            return False
        try:
            service = FolderTranslationService(
                self._build_translation_provider(), self._thai_font_path
            )
        except Exception as error:
            self._report_error(f"Could not translate folder images: {error}")
            return False
        return self._start_background_worker(
            FolderTranslationWorker(service, self._project, self._project_dir),
            "Translate folder images",
            self._workflow_progress,
            self._folder_translation_completed,
        )

    def start_export(
        self,
        output_dir: Path | str,
        font_path: Path | str,
        background_color: str = "white",
    ) -> bool:
        if self._project is None or self._project_dir is None:
            self._report_error("Could not start export: open a saved project first")
            return False
        if self.is_busy:
            return False
        worker = ExportWorker(
            self._project,
            self._project_dir,
            output_dir,
            font_path,
            background_color,
            clean_background=True,
        )
        return self._start_background_worker(
            worker,
            "Export",
            self._export_progress,
            self._export_completed,
        )

    def refresh_thai_preview(self) -> bool:
        page = self._current_page()
        if page is None or self._project_dir is None or self.is_busy:
            return False
        if not self._ensure_thai_font():
            return False
        destination = self._project_dir / "previews" / f"preview-page-{page.id}.png"
        worker = PreviewWorker(page, self._project_dir, destination, self._thai_font_path)
        return self._start_background_worker(
            worker,
            "Thai preview",
            lambda _update: None,
            self._preview_completed,
        )

    def show_source_image(self) -> bool:
        page = self._current_page()
        if page is None:
            return False
        path = Path(page.source_path)
        if not path.is_absolute() and self._project_dir is not None:
            path = self._project_dir / path
        try:
            self.image_viewer.set_display_image(path)
        except Exception as error:
            self._report_error(f"Could not show source image: {error}")
            return False
        self.statusBar().showMessage(f"Showing source image: {path.name}")
        return True

    def _start_ocr(
        self,
        page_ids: tuple[UUID, ...] | None,
        block_ids: tuple[UUID, ...] | None,
        name: str,
    ) -> bool:
        if self._project is None or self.is_busy:
            return False
        try:
            detector, ocr_provider = self._build_ocr_providers()
        except Exception as error:
            self._report_error(f"Could not start OCR: {error}")
            return False
        return self._start_workflow(
            WorkflowService(
                detector,
                ocr_provider,
                MockTranslationProvider(),
            ),
            name,
            page_ids=page_ids,
            block_ids=block_ids,
            mode="ocr",
        )

    def _start_translation(
        self,
        page_ids: tuple[UUID, ...] | None,
        block_ids: tuple[UUID, ...] | None,
        name: str,
    ) -> bool:
        if self._project is None or self.is_busy:
            return False
        if self._translation_configuration is None and not self._configure_translation_provider():
            return False
        try:
            provider = self._build_translation_provider()
        except Exception as error:
            self._report_error(f"Could not start translation: {error}")
            return False
        started = self._start_workflow(
            WorkflowService(
                MockTextDetectionProvider(),
                MockOcrProvider(),
                provider,
            ),
            name,
            page_ids=page_ids,
            block_ids=block_ids,
            mode="translate",
        )
        if started:
            self._refresh_preview_after_translation = True
        return started

    def _start_workflow(
        self,
        service: WorkflowService,
        name: str,
        *,
        page_ids: tuple[UUID, ...] | None = None,
        block_ids: tuple[UUID, ...] | None = None,
        mode: WorkflowMode = "end_to_end",
    ) -> bool:
        if self._project is None or self.is_busy:
            return False
        worker = WorkflowWorker(
            service,
            self._project.model_copy(deep=True),
            project_dir=self._project_dir,
            page_ids=page_ids,
            block_ids=block_ids,
            mode=mode,
        )
        return self._start_background_worker(
            worker,
            name,
            self._workflow_progress,
            self._workflow_completed,
        )

    def _start_background_worker(
        self,
        worker: WorkflowWorker | FolderTranslationWorker | ExportWorker | PreviewWorker,
        name: str,
        progress_callback: Callable[[object], None],
        completed_callback: Callable[[object], None],
    ) -> bool:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(progress_callback)
        worker.completed.connect(completed_callback)
        worker.cancelled.connect(self._workflow_cancelled)
        worker.failed.connect(self._workflow_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._workflow_thread_finished)

        self._workflow_thread = thread
        self._workflow_worker = worker
        self._workflow_name = name
        self._refresh_preview_after_translation = False
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_label.setText(f"Starting {name}…")
        self.progress_dock.show()
        self._append_log(f"{name} started.")
        self.statusBar().showMessage(f"{name} running…")
        self._update_action_states()
        thread.start()
        return True

    def cancel_workflow(self) -> None:
        if self._workflow_worker is not None:
            self._workflow_worker.cancel()
            self.statusBar().showMessage(f"Cancelling {self._workflow_name}…")

    def select_previous_page(self) -> None:
        if self.page_sidebar.currentRow() > 0:
            self.page_sidebar.setCurrentRow(self.page_sidebar.currentRow() - 1)

    def select_next_page(self) -> None:
        row = self.page_sidebar.currentRow()
        if 0 <= row < self.page_sidebar.count() - 1:
            self.page_sidebar.setCurrentRow(row + 1)

    def _fit_viewers(self) -> None:
        self.image_viewer.fit_to_window()
        self.preview_viewer.fit_to_window()

    def _reset_viewers(self) -> None:
        self.image_viewer.reset_zoom()
        self.preview_viewer.reset_zoom()

    def _zoom_in_viewers(self) -> None:
        self.image_viewer.zoom_in()
        self.preview_viewer.zoom_in()

    def _zoom_out_viewers(self) -> None:
        self.image_viewer.zoom_out()
        self.preview_viewer.zoom_out()

    def select_previous_block(self) -> None:
        self._select_adjacent_block(-1)

    def select_next_block(self) -> None:
        self._select_adjacent_block(1)

    def move_block_earlier(self) -> None:
        self._move_selected_block(-1)

    def move_block_later(self) -> None:
        self._move_selected_block(1)

    def _create_actions(self) -> None:
        self.open_images_action = self._action(
            "Open Images…", QKeySequence.StandardKey.Open, self._choose_image_files
        )
        self.open_image_folder_action = self._action(
            "Open Image Folder…", "Ctrl+Shift+O", self._choose_image_folder
        )
        self.new_project_action = self._action(
            "New Project…", QKeySequence.StandardKey.New, self._choose_new_project
        )
        self.open_action = self._action("Open Project…", None, self._choose_project_directory)
        self.import_images_action = self._action(
            "Import Images…", "Ctrl+I", self._choose_images_to_import
        )
        self.save_action = self._action("Save", QKeySequence.StandardKey.Save, self.save_project)
        self.export_action = self._action("Export…", None, self._choose_export)
        self.run_workflow_action = self._action("Run Workflow", "Ctrl+R", self.run_workflow)
        self.undo_workflow_action = self._action(
            "Undo Last Workflow", None, self.undo_last_workflow
        )
        self.undo_workflow_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_workflow_shortcut.activated.connect(self._dispatch_undo)
        self.translate_folder_action = self._action(
            "Translate Folder Images", None, self.translate_folder_images
        )
        self.configure_translation_action = self._action(
            "Configure Translation Provider…", None, self._configure_translation_provider
        )
        self.choose_thai_font_action = self._action(
            "Choose Thai Font…", None, self._choose_thai_font
        )
        self.refresh_preview_action = self._action(
            "Refresh Thai Preview", None, self.refresh_thai_preview
        )
        self.show_source_action = self._action("Show Source Image", None, self.show_source_image)
        self.ocr_selected_action = self._action("OCR Selected Block", None, self.ocr_selected_block)
        self.ocr_page_action = self._action("OCR Current Page", None, self.ocr_current_page)
        self.ocr_all_action = self._action("OCR All Pages", None, self.ocr_all_pages)
        self.confirm_ocr_page_action = self._action(
            "Confirm OCR for Current Page", None, self.confirm_ocr_current_page
        )
        self.translate_selected_action = self._action(
            "Translate Selected Block", None, self.translate_selected_block
        )
        self.translate_page_action = self._action(
            "Translate Current Page", None, self.translate_current_page
        )
        self.translate_all_action = self._action(
            "Translate All Pages", None, self.translate_all_pages
        )
        self.cancel_workflow_action = self._action(
            "Cancel Workflow", QKeySequence.StandardKey.Cancel, self.cancel_workflow
        )
        self.previous_page_action = self._action(
            "Previous Page", "Ctrl+PgUp", self.select_previous_page
        )
        self.next_page_action = self._action("Next Page", "Ctrl+PgDown", self.select_next_page)
        self.previous_block_action = self._action(
            "Previous Block", "Alt+Up", self.select_previous_block
        )
        self.next_block_action = self._action("Next Block", "Alt+Down", self.select_next_block)
        self.move_block_earlier_action = self._action(
            "Move Block Earlier", "Ctrl+Alt+Up", self.move_block_earlier
        )
        self.move_block_later_action = self._action(
            "Move Block Later", "Ctrl+Alt+Down", self.move_block_later
        )
        self.draw_block_action = self._action(
            "Draw Block", "B", self.image_viewer.set_draw_mode, checkable=True
        )
        self.delete_block_action = self._action(
            "Delete Block", QKeySequence.StandardKey.Delete, self._delete_selected_block
        )
        self.fit_action = self._action("Fit to Window", "F", self._fit_viewers)
        self.reset_zoom_action = self._action("Reset Zoom", "1", self._reset_viewers)
        self.zoom_in_action = self._action(
            "Zoom In", QKeySequence.StandardKey.ZoomIn, self._zoom_in_viewers
        )
        self.zoom_out_action = self._action(
            "Zoom Out", QKeySequence.StandardKey.ZoomOut, self._zoom_out_viewers
        )

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions(
            (
                self.open_images_action,
                self.open_image_folder_action,
                self.new_project_action,
                self.open_action,
                self.import_images_action,
                self.save_action,
                self.export_action,
            )
        )
        workflow_menu = self.menuBar().addMenu("&Workflow")
        workflow_menu.addActions(
            (
                self.translate_folder_action,
                self.run_workflow_action,
                self.undo_workflow_action,
                self.ocr_selected_action,
                self.ocr_page_action,
                self.ocr_all_action,
                self.confirm_ocr_page_action,
                self.translate_selected_action,
                self.translate_page_action,
                self.translate_all_action,
                self.cancel_workflow_action,
            )
        )
        settings_menu = self.menuBar().addMenu("&Settings")
        settings_menu.addActions((self.configure_translation_action, self.choose_thai_font_action))
        navigate_menu = self.menuBar().addMenu("&Navigate")
        navigate_menu.addActions(
            (
                self.previous_page_action,
                self.next_page_action,
                self.previous_block_action,
                self.next_block_action,
                self.move_block_earlier_action,
                self.move_block_later_action,
            )
        )
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addActions(
            (
                self.draw_block_action,
                self.delete_block_action,
                self.refresh_preview_action,
                self.show_source_action,
                self.fit_action,
                self.reset_zoom_action,
                self.zoom_in_action,
                self.zoom_out_action,
            )
        )

        toolbar = QToolBar("Main")
        toolbar.setObjectName("main_toolbar")
        toolbar.addActions(
            (
                self.open_images_action,
                self.open_image_folder_action,
                self.new_project_action,
                self.open_action,
                self.import_images_action,
                self.save_action,
                self.export_action,
                self.run_workflow_action,
                self.undo_workflow_action,
                self.translate_folder_action,
                self.configure_translation_action,
                self.cancel_workflow_action,
                self.previous_page_action,
                self.next_page_action,
                self.previous_block_action,
                self.next_block_action,
                self.move_block_earlier_action,
                self.move_block_later_action,
                self.draw_block_action,
                self.delete_block_action,
                self.fit_action,
                self.reset_zoom_action,
                self.zoom_in_action,
                self.zoom_out_action,
            )
        )
        self.addToolBar(toolbar)
        self.toolbar = toolbar
        self._update_action_states()

    def _action(
        self,
        text: str,
        shortcut: QKeySequence.StandardKey | QKeySequence | str | None,
        callback,
        *,
        checkable: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.setCheckable(checkable)
        action.triggered.connect(callback)
        return action

    def _connect_widgets(self) -> None:
        self.page_sidebar.page_selected.connect(self._select_page)
        self.image_viewer.block_selected.connect(self._select_block)
        self.image_viewer.block_created.connect(self._create_block)
        self.image_viewer.block_changed.connect(self._replace_block)
        self.image_viewer.block_deleted.connect(self._delete_block)
        self.block_editor.block_changed.connect(self._edit_block)
        self.block_editor.delete_requested.connect(self._delete_block_from_editor)

    def _choose_project_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Project Directory")
        if directory:
            self.open_project(directory)

    def _choose_image_files(self) -> None:
        images, _ = QFileDialog.getOpenFileNames(
            self,
            "Open Images",
            filter="Images (*.png *.jpg *.jpeg *.webp)",
        )
        if images:
            self.open_image_files(images)

    def _choose_image_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Open Image Folder")
        if directory:
            self.open_image_folder(directory)

    def _choose_new_project(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Project", "Project name:")
        if not accepted:
            return
        images, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose Initial Images",
            filter="Images (*.png *.jpg *.jpeg *.webp)",
        )
        if not images:
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose Empty Project Directory")
        if not directory:
            return
        language, accepted = QInputDialog.getItem(
            self,
            "Source Language",
            "Default source language:",
            [item.value for item in SourceLanguage],
            editable=False,
        )
        if not accepted:
            return
        reading_order, accepted = QInputDialog.getItem(
            self,
            "Reading Order",
            "Default reading order:",
            [item.value for item in ReadingOrderPreset],
            editable=False,
        )
        if not accepted:
            return
        storage, accepted = QInputDialog.getItem(
            self,
            "Image Storage",
            "Store source images by:",
            ["Copy into project", "Reference original files"],
            editable=False,
        )
        if accepted:
            self.create_project(
                directory,
                name,
                images,
                SourceLanguage(language),
                ReadingOrderPreset(reading_order),
                storage == "Copy into project",
            )

    def _choose_images_to_import(self) -> None:
        images, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Images",
            filter="Images (*.png *.jpg *.jpeg *.webp)",
        )
        if not images:
            return
        storage, accepted = QInputDialog.getItem(
            self,
            "Image Storage",
            "Store source images by:",
            ["Copy into project", "Reference original files"],
            editable=False,
        )
        if accepted:
            self.import_images(images, storage == "Copy into project")

    def _choose_export(self) -> None:
        output_dir = QFileDialog.getExistingDirectory(self, "Choose Export Directory")
        if not output_dir:
            return
        if self._ensure_thai_font():
            self.start_export(output_dir, self._thai_font_path)

    def _choose_thai_font(self) -> bool:
        font_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Thai TrueType/OpenType Font",
            filter="Fonts (*.ttf *.otf *.ttc)",
        )
        if not font_path:
            return False
        path = Path(font_path)
        if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
            self._report_error("Could not select Thai font: choose a TTF, OTF, or TTC file")
            return False
        try:
            ImageFont.truetype(path, 12)
        except OSError:
            self._report_error("Could not select Thai font: invalid font file")
            return False
        self._thai_font_path = path
        message = f"Thai font selected: {path.name}"
        self.choose_thai_font_action.setToolTip(message)
        self.statusBar().showMessage(message)
        self._append_log(message)
        return True

    def _ensure_thai_font(self) -> bool:
        return self._thai_font_path is not None or self._choose_thai_font()

    def _configure_translation_provider(self) -> bool:
        dialog = TranslationProviderDialog(self._translation_configuration, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.set_translation_configuration(dialog.configuration())
        return True

    def _create_language_toolbar(self) -> None:
        self.project_language_combo = QComboBox()
        for language in SourceLanguage:
            self.project_language_combo.addItem(language.value, language.value)

        self.page_language_combo = QComboBox()
        self.page_language_combo.addItem("inherit", None)
        for language in SourceLanguage:
            self.page_language_combo.addItem(language.value, language.value)

        self.ocr_mode_combo = QComboBox()
        self.ocr_mode_combo.addItem("Mock (offline)", "mock")
        self.ocr_mode_combo.addItem("Installed local", "installed-local")
        self.effective_provider_label = QLabel("Effective: no page")

        toolbar = QToolBar("Language and OCR")
        toolbar.setObjectName("language_ocr_toolbar")
        toolbar.addWidget(QLabel("Project language"))
        toolbar.addWidget(self.project_language_combo)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Page override"))
        toolbar.addWidget(self.page_language_combo)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("OCR mode"))
        toolbar.addWidget(self.ocr_mode_combo)
        toolbar.addSeparator()
        toolbar.addWidget(self.effective_provider_label)
        self.addToolBar(toolbar)
        self.language_toolbar = toolbar

        self.project_language_combo.currentIndexChanged.connect(self._project_language_changed)
        self.page_language_combo.currentIndexChanged.connect(self._page_language_changed)
        self.ocr_mode_combo.currentIndexChanged.connect(self._update_effective_provider_label)

    def _project_language_changed(self, *_args: object) -> None:
        if self._project is None or self.is_busy:
            return
        self._project.settings.default_source_language = SourceLanguage(
            self.project_language_combo.currentData()
        )
        self._update_effective_provider_label()

    def _page_language_changed(self, *_args: object) -> None:
        page = self._current_page()
        if page is None or self.is_busy:
            return
        value = self.page_language_combo.currentData()
        page.source_language = SourceLanguage(value) if value is not None else None
        self._update_effective_provider_label()

    def _sync_language_controls(self) -> None:
        project_language = (
            self._project.settings.default_source_language.value
            if self._project is not None
            else SourceLanguage.AUTO.value
        )
        page = self._current_page()
        page_language = page.source_language.value if page and page.source_language else None
        with (
            QSignalBlocker(self.project_language_combo),
            QSignalBlocker(self.page_language_combo),
        ):
            self.project_language_combo.setCurrentIndex(
                self.project_language_combo.findData(project_language)
            )
            self.page_language_combo.setCurrentIndex(
                self.page_language_combo.findData(page_language)
            )
        self._update_effective_provider_label()

    def _update_effective_provider_label(self, *_args: object) -> None:
        if self._project is None:
            self.effective_provider_label.setText("Effective: no project")
            return
        page = self._current_page()
        if page is None:
            self.effective_provider_label.setText("Effective: no page")
            return
        language = resolve_source_language(self._project.settings, page, self._current_block())
        if language is SourceLanguage.AUTO:
            detail = "review required"
        elif self.ocr_mode_combo.currentData() == "mock":
            detail = "mock-ocr"
        elif language is SourceLanguage.JA:
            detail = "manga-ocr"
        else:
            detail = "paddleocr"
        self.effective_provider_label.setText(f"Effective: {language.value} · OCR: {detail}")

    def _build_ocr_providers(
        self,
    ) -> tuple[MockTextDetectionProvider | PaddleOcrProvider, OcrRouter]:
        concrete = tuple(
            language for language in SourceLanguage if language is not SourceLanguage.AUTO
        )
        if self.ocr_mode_combo.currentData() == "mock":
            provider = MockOcrProvider()
            return (
                MockTextDetectionProvider(),
                OcrRouter(
                    OcrProviderRegistry([provider]),
                    {language: provider.name for language in concrete},
                ),
            )
        if self.ocr_mode_combo.currentData() != "installed-local":
            raise ValueError("unsupported OCR mode")
        japanese = MangaOcrProvider(force_cpu=True)
        multilingual = PaddleOcrProvider(device="cpu")
        return (
            multilingual,
            OcrRouter(
                OcrProviderRegistry([japanese, multilingual]),
                {
                    SourceLanguage.JA: japanese.name,
                    SourceLanguage.EN: multilingual.name,
                    SourceLanguage.KO: multilingual.name,
                    SourceLanguage.ZH_HANS: multilingual.name,
                    SourceLanguage.ZH_HANT: multilingual.name,
                },
            ),
        )

    def _build_translation_provider(self) -> TranslationProvider:
        configuration = self._translation_configuration
        if configuration is None:
            raise ValueError("configure a translation provider first")
        if configuration.provider == "openai-compatible":
            return OpenAICompatibleTranslationProvider(
                configuration,
                api_key=resolve_api_key(configuration),
            )
        if configuration.provider == "ollama":
            return OllamaTranslationProvider(configuration)
        if configuration.provider == "codex-cli":
            return CodexCliTranslationProvider(configuration)
        raise ValueError(f"unsupported translation provider: {configuration.provider}")

    def _create_progress_dock(self) -> None:
        self.progress_label = QLabel("No workflow has run.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.workflow_log = QPlainTextEdit()
        self.workflow_log.setReadOnly(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.workflow_log)
        self.progress_dock = QDockWidget("Workflow Progress", self)
        self.progress_dock.setObjectName("workflow_progress_dock")
        self.progress_dock.setWidget(content)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.progress_dock)

    def _current_page(self) -> Page | None:
        if self._project is None or self._current_page_id is None:
            return None
        return next(
            (page for page in self._project.pages if page.id == self._current_page_id),
            None,
        )

    def _current_block(self) -> TextBlock | None:
        page = self._current_page()
        selected_id = self._current_block_id
        if page is None or selected_id is None:
            return None
        return next((block for block in page.blocks if block.id == selected_id), None)

    def _select_page(self, page_id: UUID) -> None:
        if self._project is None:
            return
        page = next(
            (candidate for candidate in self._project.pages if candidate.id == page_id), None
        )
        if page is None:
            return

        self._current_page_id = page.id
        self._current_block_id = None
        self.block_editor.set_block(None)
        self.preview_viewer.set_page(None)
        self._sync_language_controls()
        image_path = Path(page.source_path)
        if not image_path.is_absolute() and self._project_dir is not None:
            image_path = self._project_dir / image_path
        if not image_path.is_file():
            self.image_viewer.set_page(None)
            self.statusBar().showMessage(f"Could not load image: {image_path}")
            return

        viewer_page = page.model_copy(update={"source_path": str(image_path)})
        try:
            self.image_viewer.set_page(viewer_page)
        except Exception as error:
            self.image_viewer.set_page(None)
            self.statusBar().showMessage(f"Could not load image: {error}")
            return
        self._load_page_preview(page)
        self._fit_viewers()
        self.statusBar().showMessage(
            f"Page {self.page_sidebar.currentRow() + 1}: {image_path.name}"
        )
        self._update_action_states()

    def _load_page_preview(self, page: Page) -> None:
        if self._project_dir is None:
            self.preview_viewer.set_page(None)
            return
        path = self._project_dir / "previews" / f"preview-page-{page.id}.png"
        if not path.is_file():
            self.preview_viewer.set_page(None)
            return
        try:
            self.preview_viewer.set_page(
                page.model_copy(update={"source_path": str(path), "blocks": []})
            )
        except Exception as error:
            self.preview_viewer.set_page(None)
            self._append_log(f"Could not load Thai preview: {error}")

    def _select_block(self, block_id: UUID) -> None:
        page = self._current_page()
        if page is None:
            self.block_editor.set_block(None)
            return
        block = next((candidate for candidate in page.blocks if candidate.id == block_id), None)
        self._current_block_id = block.id if block is not None else None
        self.block_editor.set_block(block)
        self._update_effective_provider_label()
        self._update_action_states()

    def _create_block(self, block: TextBlock) -> None:
        page = self._current_page()
        if page is None:
            return
        if block.page_id != page.id:
            block = block.model_copy(update={"page_id": page.id})
        if any(candidate.id == block.id for candidate in page.blocks):
            self.statusBar().showMessage(f"Block already exists: {block.id}")
            return
        page.blocks.append(block)
        self._refresh_sidebar()
        self.image_viewer.select_block(block.id)
        self.block_editor.set_block(block)

    def _replace_block(self, block: TextBlock, *, update_editor: bool = True) -> None:
        page = self._current_page()
        if page is None or block.page_id != page.id:
            return
        for index, candidate in enumerate(page.blocks):
            if candidate.id == block.id:
                page.blocks[index] = block
                if update_editor:
                    self.block_editor.set_block(block)
                return

    def _edit_block(self, block: TextBlock) -> None:
        self._replace_block(block, update_editor=False)
        self.image_viewer.update_block(block)
        self._update_effective_provider_label()
        self._update_action_states()

    def _delete_block_from_editor(self, block_id: UUID) -> None:
        self.image_viewer.select_block(block_id)
        self.image_viewer.delete_selected_block()

    def _delete_selected_block(self) -> None:
        self.image_viewer.delete_selected_block()

    def _delete_block(self, block_id: UUID) -> None:
        page = self._current_page()
        if page is None:
            return
        original_count = len(page.blocks)
        page.blocks[:] = [block for block in page.blocks if block.id != block_id]
        if len(page.blocks) == original_count:
            return
        if self._current_block_id == block_id:
            self._current_block_id = None
        self.block_editor.set_block(None)
        self._refresh_sidebar()

    def _refresh_sidebar(self) -> None:
        if self._project is None:
            return
        current_id = self._current_page_id
        self.page_sidebar.set_pages(self._project.pages)
        if current_id is not None:
            self.page_sidebar.select_page(current_id)

    def _replace_project_preserving_page(self, project: Project) -> None:
        self._replace_project_with_selection(project, self._current_page_id, self._current_block_id)

    def _replace_project_with_selection(
        self,
        project: Project,
        page_id: UUID | None,
        block_id: UUID | None,
    ) -> None:
        project_dir = self._project_dir
        self.set_project(project, project_dir)
        if page_id is not None and any(page.id == page_id for page in project.pages):
            self.page_sidebar.select_page(page_id)
        page = self._current_page()
        if (
            block_id is not None
            and page is not None
            and any(block.id == block_id for block in page.blocks)
        ):
            self.image_viewer.select_block(block_id)
            self._select_block(block_id)

    def _remember_workflow_change(self, project: Project) -> None:
        if self._project is None or self._project == project:
            return
        self._workflow_undo_history.append(
            (
                self._project.model_copy(deep=True),
                self._current_page_id,
                self._current_block_id,
            )
        )
        del self._workflow_undo_history[:-_WORKFLOW_UNDO_LIMIT]

    def _clear_stale_previews(self, project: Project) -> None:
        if self._project_dir is None:
            return
        for page in project.pages:
            path = self._project_dir / "previews" / f"preview-page-{page.id}.png"
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                self._append_log(f"Could not clear stale Thai preview: {error}")

    def _workflow_progress(self, update: ProgressUpdate) -> None:
        self.progress_bar.setRange(0, max(1, update.total))
        self.progress_bar.setValue(update.current)
        message = f"{update.stage.title()} {update.current}/{update.total}: {update.message}"
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _export_progress(self, update: ExportProgress) -> None:
        self.progress_bar.setRange(0, max(1, update.total))
        self.progress_bar.setValue(update.current)
        message = f"Export {update.current}/{update.total}: {update.message}"
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _workflow_completed(self, result: WorkflowResult) -> None:
        previous_page = self._current_page()
        previous_translations = (
            {block.id: block.translated_text for block in previous_page.blocks}
            if self._refresh_preview_after_translation and previous_page is not None
            else {}
        )
        self._remember_workflow_change(result.project)
        self._replace_project_preserving_page(result.project)
        if self._refresh_preview_after_translation:
            page = self._current_page()
            self._refresh_preview_after_translation = page is not None and any(
                block.id in previous_translations
                and block.translated_text != previous_translations[block.id]
                for block in page.blocks
            )
        for issue in result.issues:
            self._append_log(self._format_issue(issue))
        message = f"{self._workflow_name} completed with {len(result.issues)} issue(s)."
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _folder_translation_completed(self, result: WorkflowResult) -> None:
        self._remember_workflow_change(result.project)
        self._replace_project_preserving_page(result.project)
        for issue in result.issues:
            self._append_log(self._format_issue(issue))
        if not self.save_project():
            self._report_error("Could not persist translated folder")
            return
        page = self._current_page()
        if page is not None:
            self._load_page_preview(page)
        message = f"Translate folder images completed with {len(result.issues)} issue(s)."
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _export_completed(self, result: ExportResult) -> None:
        for path in (
            result.json_path,
            result.csv_path,
            result.txt_path,
            *result.preview_paths,
        ):
            self._append_log(f"Exported: {path}")
        for issue in result.issues:
            self._append_log(self._format_export_issue(issue))
        for warning in result.overflow_warnings:
            self._append_log(
                f"Overflow warning: {warning.message} "
                f"(page={warning.page_id}, block={warning.block_id})"
            )
        message = (
            f"Export completed with {len(result.issues)} issue(s) and "
            f"{len(result.overflow_warnings)} overflow warning(s)."
        )
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _preview_completed(self, result: tuple[UUID, Path, tuple, tuple]) -> None:
        page_id, path, warnings, issues = result
        for issue in issues:
            self._append_log(self._format_export_issue(issue))
        for warning in warnings:
            self._append_log(
                f"Overflow warning: {warning.message} "
                f"(page={warning.page_id}, block={warning.block_id})"
            )
        if self._current_page_id == page_id:
            try:
                page = self._current_page()
                self.preview_viewer.set_page(
                    page.model_copy(update={"source_path": str(path), "blocks": []})
                )
                self.preview_viewer.fit_to_window()
            except Exception as error:
                self._report_error(f"Could not show Thai preview: {error}")
                return
        message = f"Thai preview refreshed with {len(issues)} issue(s)."
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _workflow_cancelled(self) -> None:
        self._refresh_preview_after_translation = False
        message = f"{self._workflow_name} cancelled."
        self.progress_label.setText(message)
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _workflow_failed(self, message: str) -> None:
        self._refresh_preview_after_translation = False
        self._report_error(f"{self._workflow_name} failed: {message}")

    def _workflow_thread_finished(self) -> None:
        refresh_preview = self._refresh_preview_after_translation
        self._refresh_preview_after_translation = False
        self._workflow_thread = None
        self._workflow_worker = None
        self._update_action_states()
        if refresh_preview:
            self.refresh_thai_preview()

    def _update_action_states(self) -> None:
        if not hasattr(self, "new_project_action"):
            return
        busy = self.is_busy
        self.splitter.setEnabled(not busy)
        has_project = self._project is not None
        has_page = self._current_page() is not None
        has_project_dir = self._project_dir is not None
        row = self.page_sidebar.currentRow()
        for action in (
            self.open_images_action,
            self.open_image_folder_action,
            self.new_project_action,
            self.open_action,
        ):
            action.setEnabled(not busy)
        self.configure_translation_action.setEnabled(not busy)
        self.choose_thai_font_action.setEnabled(not busy)
        self.import_images_action.setEnabled(has_project and has_project_dir and not busy)
        self.save_action.setEnabled(has_project and has_project_dir and not busy)
        self.export_action.setEnabled(has_project and has_project_dir and not busy)
        self.run_workflow_action.setEnabled(has_project and bool(self._project.pages) and not busy)
        self.undo_workflow_action.setEnabled(bool(self._workflow_undo_history) and not busy)
        configuration = self._translation_configuration
        self.translate_folder_action.setEnabled(
            has_project
            and has_project_dir
            and bool(self._project.pages)
            and configuration is not None
            and configuration.provider == "codex-cli"
            and configuration.uploads_images
            and not busy
        )
        self.cancel_workflow_action.setEnabled(busy)
        self.previous_page_action.setEnabled(has_page and row > 0 and not busy)
        self.next_page_action.setEnabled(
            has_page and row < self.page_sidebar.count() - 1 and not busy
        )
        page = self._current_page()
        has_blocks = page is not None and bool(page.blocks)
        has_selected_block = self._current_block() is not None
        self.ocr_selected_action.setEnabled(has_selected_block and not busy)
        self.ocr_page_action.setEnabled(has_page and not busy)
        self.ocr_all_action.setEnabled(has_project and bool(self._project.pages) and not busy)
        self.confirm_ocr_page_action.setEnabled(
            not busy
            and page is not None
            and any(block.status is BlockStatus.OCR_COMPLETE for block in page.blocks)
        )
        self.translate_selected_action.setEnabled(has_selected_block and not busy)
        self.translate_page_action.setEnabled(has_page and not busy)
        self.translate_all_action.setEnabled(has_project and bool(self._project.pages) and not busy)
        self.previous_block_action.setEnabled(has_blocks and not busy)
        self.next_block_action.setEnabled(has_blocks and not busy)
        ordered_blocks = (
            sorted(page.blocks, key=lambda block: (block.reading_order, str(block.id)))
            if page is not None
            else []
        )
        selected_index = next(
            (
                index
                for index, block in enumerate(ordered_blocks)
                if block.id == self._current_block_id
            ),
            None,
        )
        self.move_block_earlier_action.setEnabled(
            selected_index is not None and selected_index > 0 and not busy
        )
        self.move_block_later_action.setEnabled(
            selected_index is not None and selected_index < len(ordered_blocks) - 1 and not busy
        )
        self.draw_block_action.setEnabled(has_page and not busy)
        self.refresh_preview_action.setEnabled(has_page and has_project_dir and not busy)
        self.show_source_action.setEnabled(has_page and not busy)
        self.delete_block_action.setEnabled(
            has_page and self.image_viewer.selected_block_id is not None and not busy
        )
        for action in (
            self.fit_action,
            self.reset_zoom_action,
            self.zoom_in_action,
            self.zoom_out_action,
        ):
            action.setEnabled(has_page and not busy)
        self.project_language_combo.setEnabled(has_project and not busy)
        self.page_language_combo.setEnabled(has_page and not busy)
        self.ocr_mode_combo.setEnabled(has_project and not busy)

    def _report_error(self, message: str) -> None:
        self.progress_dock.show()
        self.statusBar().showMessage(message)
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        self.workflow_log.appendPlainText(message)

    @staticmethod
    def _format_issue(issue: WorkflowIssue) -> str:
        location = []
        if issue.page_id is not None:
            location.append(f"page={issue.page_id}")
        if issue.block_id is not None:
            location.append(f"block={issue.block_id}")
        suffix = f" ({', '.join(location)})" if location else ""
        return f"{issue.stage.title()} issue: {issue.message}{suffix}"

    @staticmethod
    def _format_export_issue(issue: ExportIssue) -> str:
        location = []
        if issue.page_id is not None:
            location.append(f"page={issue.page_id}")
        if issue.block_id is not None:
            location.append(f"block={issue.block_id}")
        suffix = f" ({', '.join(location)})" if location else ""
        return f"Export issue: {issue.message}{suffix}"

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._workflow_worker is not None:
            self._workflow_worker.cancel()
        if self._workflow_thread is not None:
            self._workflow_thread.quit()
            if not self._workflow_thread.wait(_CLOSE_WAIT_MS):
                message = "Workflow is still stopping; close the window again when it finishes."
                self.statusBar().showMessage(message)
                self._append_log(message)
                event.ignore()
                return
        event.accept()

    def _select_adjacent_block(self, offset: int) -> None:
        page = self._current_page()
        if page is None or not page.blocks:
            return
        blocks = sorted(page.blocks, key=lambda block: (block.reading_order, str(block.id)))
        selected_id = self.image_viewer.selected_block_id
        if selected_id is None:
            target = blocks[0 if offset > 0 else -1]
        else:
            current = next(
                (index for index, block in enumerate(blocks) if block.id == selected_id),
                None,
            )
            if current is None:
                target = blocks[0 if offset > 0 else -1]
            else:
                target = blocks[max(0, min(len(blocks) - 1, current + offset))]
        self.image_viewer.select_block(target.id)
        self.block_editor.set_block(target)

    def _move_selected_block(self, offset: int) -> None:
        if self.is_busy:
            return
        page = self._current_page()
        selected_id = self._current_block_id
        if page is None or selected_id is None:
            return
        blocks = sorted(page.blocks, key=lambda block: (block.reading_order, str(block.id)))
        current = next(
            (index for index, block in enumerate(blocks) if block.id == selected_id),
            None,
        )
        if current is None or not 0 <= current + offset < len(blocks):
            return
        blocks[current], blocks[current + offset] = blocks[current + offset], blocks[current]
        page.blocks = normalize_reading_order(blocks)
        self._refresh_sidebar()
        self.image_viewer.select_block(selected_id)
        self._select_block(selected_id)
