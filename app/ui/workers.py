"""Qt workers for running services off the GUI thread."""

import asyncio
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from app.core.models import Project
from app.services.export import ExportService
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
    ) -> None:
        super().__init__()
        self._project = project.model_copy(deep=True)
        self._project_dir = project_dir
        self._output_dir = output_dir
        self._font_path = font_path
        self._background_color = background_color
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
