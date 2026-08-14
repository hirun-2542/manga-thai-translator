"""Qt workers for running services off the GUI thread."""

import asyncio
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from app.core.models import Page, Project
from app.services.export import ExportService
from app.services.folder_translation import FolderTranslationService
from app.services.iopaint_cleanup import IOPaintCleanupService
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
        self._close_lock = threading.Lock()
        self._closed = False

    def _close_service(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self._service, "close", None)
        if callable(close):
            close()

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
            try:
                self._close_service()
            except Exception as error:
                self.failed.emit(f"Workflow close failed ({type(error).__name__})")
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()
        try:
            self._close_service()
        except Exception as error:
            self.failed.emit(f"Workflow close failed ({type(error).__name__})")


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
        watermark_text: str | None = None,
        watermark_logo_path: str | Path | None = None,
        banner_path: str | Path | None = None,
        banner_position: str = "end",
    ) -> None:
        super().__init__()
        self._project = project.model_copy(deep=True)
        self._project_dir = project_dir
        self._output_dir = output_dir
        self._font_path = font_path
        self._background_color = background_color
        self._clean_background = clean_background
        self._watermark_text = watermark_text
        self._watermark_logo_path = watermark_logo_path
        self._banner_path = banner_path
        self._banner_position = banner_position
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
                watermark_text=self._watermark_text,
                watermark_logo_path=self._watermark_logo_path,
                banner_path=self._banner_path,
                banner_position=self._banner_position,
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
    metric = Signal(object)
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
                on_metric=self.metric.emit,
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


@dataclass(frozen=True, slots=True)
class CleanupProgress:
    current: int
    total: int
    block_id: UUID
    message: str


@dataclass(frozen=True, slots=True)
class CleanupIssue:
    block_id: UUID
    message: str


@dataclass(frozen=True, slots=True)
class PageCleanupResult:
    paths: tuple[Path, ...]
    issues: tuple[CleanupIssue, ...]


class ImageCleanupWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: IOPaintCleanupService,
        page: Page,
        project_dir: str | Path,
        block_id: UUID,
    ) -> None:
        super().__init__()
        self._service = service
        self._page = page.model_copy(deep=True)
        self._project_dir = project_dir
        self._block_id = block_id
        self._cancellation = CancellationToken()

    @Slot()
    def run(self) -> None:
        try:
            path = self._service.clean_block(
                self._page,
                self._project_dir,
                self._block_id,
                cancellation=self._cancellation,
            )
        except WorkflowCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.completed.emit(path)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()


class PageImageCleanupWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: IOPaintCleanupService,
        page: Page,
        project_dir: str | Path,
        block_ids: Iterable[UUID],
    ) -> None:
        super().__init__()
        self._service = service
        self._page = page.model_copy(deep=True)
        self._project_dir = project_dir
        self._block_ids = tuple(block_ids)
        self._cancellation = CancellationToken()

    @Slot()
    def run(self) -> None:
        paths: list[Path] = []
        issues: list[CleanupIssue] = []
        total = len(self._block_ids)
        try:
            for current, block_id in enumerate(self._block_ids, 1):
                self._cancellation.raise_if_cancelled()
                try:
                    path = self._service.clean_block(
                        self._page,
                        self._project_dir,
                        block_id,
                        cancellation=self._cancellation,
                    )
                except WorkflowCancelled:
                    raise
                except Exception as error:
                    issues.append(
                        CleanupIssue(
                            block_id=block_id,
                            message=f"{type(error).__name__}: {error}",
                        )
                    )
                    message = "Cleanup failed; continuing"
                else:
                    paths.append(path)
                    message = "Cleanup saved"
                self.progress.emit(CleanupProgress(current, total, block_id, message))
        except WorkflowCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.completed.emit(PageCleanupResult(tuple(paths), tuple(issues)))
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancellation.cancel()
