"""Qt workers for running services off the GUI thread."""

import asyncio
import threading
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from app.core.models import Page, Project
from app.services.export import ExportService
from app.services.folder_translation import FolderTranslationService
from app.services.workflow import (
    CancellationToken,
    WorkflowCancelled,
    WorkflowMode,
    WorkflowService,
)


class WorkflowWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: WorkflowService,
        project: Project,
        project_dir: str | Path | None = None,
        page_ids: Iterable[UUID] | None = None,
        block_ids: Iterable[UUID] | None = None,
        mode: WorkflowMode = "end_to_end",
    ) -> None:
        super().__init__()
        self._service = service
        self._project = project
        self._project_dir = project_dir
        self._page_ids = None if page_ids is None else tuple(page_ids)
        self._block_ids = None if block_ids is None else tuple(block_ids)
        self._mode = mode
        self._cancellation = CancellationToken()

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(
                self._service.run(
                    self._project,
                    project_dir=self._project_dir,
                    page_ids=self._page_ids,
                    cancellation=self._cancellation,
                    on_progress=self.progress.emit,
                    block_ids=self._block_ids,
                    mode=self._mode,
                )
            )
        except WorkflowCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()


class FolderTranslationWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: FolderTranslationService,
        project: Project,
        project_dir: str | Path,
    ) -> None:
        super().__init__()
        self._service = service
        self._project = project.model_copy(deep=True)
        self._project_dir = project_dir
        self._cancellation = CancellationToken()
        self._async_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[object] | None = None

    async def _run_service(self) -> object:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        assert task is not None
        with self._async_lock:
            self._loop = loop
            self._task = task
        try:
            self._cancellation.raise_if_cancelled()
            return await self._service.run(
                self._project,
                self._project_dir,
                cancellation=self._cancellation,
                on_progress=self.progress.emit,
            )
        finally:
            with self._async_lock:
                self._loop = None
                self._task = None

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(self._run_service())
        except (WorkflowCancelled, asyncio.CancelledError):
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()
        with self._async_lock:
            if self._loop is not None and self._task is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)


class ExportWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        project: Project,
        project_dir: str | Path,
        output_dir: str | Path,
        font_path: str | Path,
        background_color: str = "white",
        service: type[ExportService] = ExportService,
        *,
        clean_background: bool = False,
    ) -> None:
        super().__init__()
        self._project = project.model_copy(deep=True)
        self._project_dir = project_dir
        self._output_dir = output_dir
        self._font_path = font_path
        self._background_color = background_color
        self._clean_background = clean_background
        self._service = service
        self._cancellation = CancellationToken()

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.export(
                self._project,
                self._project_dir,
                self._output_dir,
                self._font_path,
                background_color=self._background_color,
                clean_background=self._clean_background,
                cancellation=self._cancellation,
                on_progress=self.progress.emit,
            )
        except WorkflowCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"Export failed ({type(error).__name__})")
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()


class PreviewWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        page: Page,
        project_dir: str | Path,
        destination: str | Path,
        font_path: str | Path,
        service: type[ExportService] = ExportService,
    ) -> None:
        super().__init__()
        self._page = page.model_copy(deep=True)
        self._project_dir = project_dir
        self._destination = Path(destination)
        self._font_path = font_path
        self._service = service
        self._cancellation = CancellationToken()

    @Slot()
    def run(self) -> None:
        try:
            warnings, issues = self._service.render_page_preview(
                self._page,
                self._project_dir,
                self._destination,
                self._font_path,
                clean_background=True,
                cancellation=self._cancellation,
            )
        except WorkflowCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"Preview failed ({type(error).__name__}): {error}")
        else:
            self.completed.emit((self._page.id, self._destination, warnings, issues))
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()
