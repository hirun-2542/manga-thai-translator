import asyncio
import threading
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThread

from app.core.models import Page, Project
from app.services.workflow import CancellationToken
from app.ui.workers import ExportWorker, FolderTranslationWorker, PreviewWorker, WorkflowWorker


def _start(
    worker: WorkflowWorker | FolderTranslationWorker | ExportWorker | PreviewWorker,
) -> QThread:
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.start()
    return thread


def _wait(qapp, thread: QThread) -> None:
    deadline = time.monotonic() + 2
    while thread.isRunning() and time.monotonic() < deadline:
        qapp.processEvents()
        thread.wait(10)
    qapp.processEvents()
    assert thread.wait(100)


def test_success_emits_progress_and_runs_off_gui_thread(qapp, tmp_path: Path) -> None:
    result = object()
    page_ids = [uuid4(), uuid4()]
    gui_thread_id = threading.get_ident()

    class Service:
        async def run(self, project, **kwargs):
            self.thread_id = threading.get_ident()
            self.arguments = (project, kwargs)
            kwargs["on_progress"]("halfway")
            return result

    service = Service()
    project = Project(name="worker")
    worker = WorkflowWorker(service, project, tmp_path, page_ids)
    progress = []
    completed = []
    failed = []
    finished = []
    worker.progress.connect(progress.append)
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    _wait(qapp, thread)

    forwarded_project, arguments = service.arguments
    assert service.thread_id != gui_thread_id
    assert forwarded_project is project
    assert arguments["project_dir"] == tmp_path
    assert arguments["page_ids"] == tuple(page_ids)
    assert arguments["block_ids"] is None
    assert arguments["mode"] == "end_to_end"
    assert progress == ["halfway"]
    assert completed == [result]
    assert failed == []
    assert finished == [True]


def test_cancel_is_direct_and_cooperative(qapp) -> None:
    started = threading.Event()

    class Service:
        async def run(self, project, **kwargs):
            token = kwargs["cancellation"]
            started.set()
            while not token.is_cancelled:
                await asyncio.sleep(0.001)
            token.raise_if_cancelled()

    worker = WorkflowWorker(Service(), Project(name="cancel"))
    completed = []
    cancelled = []
    failed = []
    finished = []
    worker.completed.connect(completed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    assert started.wait(1)
    worker.cancel()
    _wait(qapp, thread)

    assert completed == []
    assert cancelled == [True]
    assert failed == []
    assert finished == [True]


def test_unexpected_error_emits_useful_message(qapp) -> None:
    class Service:
        async def run(self, project, **kwargs):
            raise ValueError("broken provider")

    worker = WorkflowWorker(Service(), Project(name="error"))
    completed = []
    cancelled = []
    failed = []
    finished = []
    worker.completed.connect(completed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    _wait(qapp, thread)

    assert completed == []
    assert cancelled == []
    assert failed == ["ValueError: broken provider"]
    assert finished == [True]


def test_folder_translation_worker_forwards_snapshot_progress_off_gui_thread(
    qapp, tmp_path
) -> None:
    result = object()
    gui_thread_id = threading.get_ident()

    class Service:
        async def run(self, project, project_dir, **kwargs):
            self.thread_id = threading.get_ident()
            self.arguments = (project, project_dir, kwargs)
            kwargs["on_progress"]("page complete")
            return result

    service = Service()
    project = Project(name="folder")
    worker = FolderTranslationWorker(service, project, tmp_path)
    project.name = "edited"
    progress = []
    completed = []
    worker.progress.connect(progress.append)
    worker.completed.connect(completed.append)

    thread = _start(worker)
    _wait(qapp, thread)

    snapshot, project_dir, kwargs = service.arguments
    assert service.thread_id != gui_thread_id
    assert snapshot.name == "folder"
    assert project_dir == tmp_path
    assert isinstance(kwargs["cancellation"], CancellationToken)
    assert progress == ["page complete"]
    assert completed == [result]


def test_folder_translation_cancel_interrupts_non_polling_await(qapp, tmp_path) -> None:
    started = threading.Event()

    class Service:
        async def run(self, project, project_dir, **kwargs):
            self.cancellation = kwargs["cancellation"]
            started.set()
            await asyncio.sleep(0.05)
            return object()

    service = Service()
    worker = FolderTranslationWorker(service, Project(name="cancel folder"), tmp_path)
    completed = []
    cancelled = []
    failed = []
    finished = []
    worker.completed.connect(completed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    assert started.wait(1)
    cancel_started = time.monotonic()
    worker.cancel()
    _wait(qapp, thread)

    assert time.monotonic() - cancel_started < 0.5
    assert service.cancellation.is_cancelled
    assert completed == []
    assert cancelled == [True]
    assert failed == []
    assert finished == [True]


def test_block_scope_and_mode_are_forwarded_without_deduplication(qapp) -> None:
    block_id = uuid4()
    cases = [
        ([], "ocr", ()),
        ([block_id, block_id], "translate", (block_id, block_id)),
    ]

    for block_ids, mode, expected in cases:

        class Service:
            async def run(self, project, **kwargs):
                self.arguments = kwargs
                return object()

        service = Service()
        worker = WorkflowWorker(
            service,
            Project(name="scope"),
            block_ids=(item for item in block_ids),
            mode=mode,
        )

        thread = _start(worker)
        _wait(qapp, thread)

        assert service.arguments["block_ids"] == expected
        assert service.arguments["mode"] == mode


def test_export_forwards_snapshot_progress_and_result_off_gui_thread(qapp, tmp_path: Path) -> None:
    result = object()
    gui_thread_id = threading.get_ident()

    class Service:
        @classmethod
        def export(cls, project, project_dir, output_dir, font_path, **kwargs):
            cls.thread_id = threading.get_ident()
            cls.arguments = (project, project_dir, output_dir, font_path, kwargs)
            kwargs["on_progress"]("preview complete")
            return result

    project = Project(name="snapshot")
    output_dir = tmp_path / "output"
    font_path = tmp_path / "font.ttf"
    worker = ExportWorker(
        project,
        tmp_path,
        output_dir,
        font_path,
        "#123456",
        Service,
    )
    project.name = "edited while export queued"
    progress = []
    completed = []
    finished = []
    worker.progress.connect(progress.append)
    worker.completed.connect(completed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    _wait(qapp, thread)

    snapshot, project_dir, forwarded_output, forwarded_font, kwargs = Service.arguments
    assert Service.thread_id != gui_thread_id
    assert snapshot is not project
    assert snapshot.name == "snapshot"
    assert project_dir == tmp_path
    assert forwarded_output == output_dir
    assert forwarded_font == font_path
    assert kwargs["background_color"] == "#123456"
    assert kwargs["clean_background"] is False
    assert isinstance(kwargs["cancellation"], CancellationToken)
    assert progress == ["preview complete"]
    assert completed == [result]
    assert finished == [True]


def test_preview_forwards_snapshot_and_clean_background_off_gui_thread(qapp, tmp_path) -> None:
    page = Page(source_path="page.png", width=20, height=30)
    destination = tmp_path / "previews" / "page.png"
    gui_thread_id = threading.get_ident()

    class Service:
        @classmethod
        def render_page_preview(cls, page, project_dir, destination, font_path, **kwargs):
            cls.thread_id = threading.get_ident()
            cls.arguments = (page, project_dir, destination, font_path, kwargs)
            return ("warning",), ("issue",)

    worker = PreviewWorker(page, tmp_path, destination, tmp_path / "font.ttf", Service)
    completed = []
    worker.completed.connect(completed.append)
    page.width = 99

    thread = _start(worker)
    _wait(qapp, thread)

    snapshot, project_dir, forwarded_destination, forwarded_font, kwargs = Service.arguments
    assert Service.thread_id != gui_thread_id
    assert snapshot.width == 20
    assert project_dir == tmp_path
    assert forwarded_destination == destination
    assert forwarded_font == tmp_path / "font.ttf"
    assert kwargs["clean_background"] is True
    assert isinstance(kwargs["cancellation"], CancellationToken)
    assert completed == [(snapshot.id, destination, ("warning",), ("issue",))]


def test_export_cancel_is_direct_and_cooperative(qapp, tmp_path: Path) -> None:
    started = threading.Event()

    class Service:
        @classmethod
        def export(cls, project, project_dir, output_dir, font_path, **kwargs):
            token = kwargs["cancellation"]
            started.set()
            while not token.is_cancelled:
                time.sleep(0.001)
            token.raise_if_cancelled()

    worker = ExportWorker(
        Project(name="cancel export"),
        tmp_path,
        tmp_path / "output",
        tmp_path / "font.ttf",
        service=Service,
    )
    completed = []
    cancelled = []
    failed = []
    finished = []
    worker.completed.connect(completed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    assert started.wait(1)
    worker.cancel()
    _wait(qapp, thread)

    assert completed == []
    assert cancelled == [True]
    assert failed == []
    assert finished == [True]


def test_export_failure_is_redacted_and_finishes_once(qapp, tmp_path: Path) -> None:
    secret = "private/source/path"

    class Service:
        @classmethod
        def export(cls, project, project_dir, output_dir, font_path, **kwargs):
            raise ValueError(secret)

    worker = ExportWorker(
        Project(name="failed export"),
        tmp_path,
        tmp_path / "output",
        tmp_path / "font.ttf",
        service=Service,
    )
    completed = []
    cancelled = []
    failed = []
    finished = []
    worker.completed.connect(completed.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    _wait(qapp, thread)

    assert completed == []
    assert cancelled == []
    assert failed == ["Export failed (ValueError)"]
    assert secret not in failed[0]
    assert finished == [True]
