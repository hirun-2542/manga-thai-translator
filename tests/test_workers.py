import asyncio
import threading
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThread

from app.core.models import BoundingBox, Page, Project, TextBlock
from app.services.export import RenderMetric
from app.services.workflow import CancellationToken
from app.ui.workers import (
    ExportWorker,
    FolderTranslationWorker,
    ImageCleanupWorker,
    PageImageCleanupWorker,
    PreviewWorker,
    WorkflowWorker,
)


def _start(
    worker: (
        WorkflowWorker
        | FolderTranslationWorker
        | ExportWorker
        | PreviewWorker
        | ImageCleanupWorker
        | PageImageCleanupWorker
    ),
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


def test_cancel_closes_workflow_service_to_unblock_native_provider(qapp) -> None:
    started = threading.Event()
    closed = threading.Event()

    class Service:
        def __init__(self) -> None:
            self.close_calls = 0

        async def run(self, project, **kwargs):
            started.set()
            await asyncio.to_thread(closed.wait)
            kwargs["cancellation"].raise_if_cancelled()

        def close(self) -> None:
            self.close_calls += 1
            closed.set()

    service = Service()
    worker = WorkflowWorker(service, Project(name="cancel native"))
    cancelled = []
    worker.cancelled.connect(lambda: cancelled.append(True))

    thread = _start(worker)
    assert started.wait(1)
    worker.cancel()
    _wait(qapp, thread)

    assert closed.is_set()
    assert service.close_calls == 1
    assert cancelled == [True]


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


def test_workflow_worker_closes_service_in_finally(qapp) -> None:
    class Service:
        def __init__(self) -> None:
            self.close_calls = 0

        async def run(self, project, **kwargs):
            raise ValueError("broken provider")

        def close(self) -> None:
            self.close_calls += 1

    service = Service()
    worker = WorkflowWorker(service, Project(name="close"))
    finished = []
    worker.finished.connect(lambda: finished.append(True))

    thread = _start(worker)
    _wait(qapp, thread)

    assert service.close_calls == 1
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


def test_image_cleanup_worker_runs_service_off_gui_thread(qapp, tmp_path: Path) -> None:
    gui_thread_id = threading.get_ident()
    page = Page(source_path="source.png", width=10, height=10)
    block_id = uuid4()
    expected = tmp_path / "cleanup.png"

    class Service:
        def clean_block(self, received_page, project_dir, received_block_id, **kwargs):
            self.thread_id = threading.get_ident()
            self.arguments = (received_page, project_dir, received_block_id, kwargs)
            return expected

    service = Service()
    worker = ImageCleanupWorker(service, page, tmp_path, block_id)
    page.source_path = "changed.png"
    completed = []
    worker.completed.connect(completed.append)

    thread = _start(worker)
    _wait(qapp, thread)

    snapshot, project_dir, received_block_id, kwargs = service.arguments
    assert service.thread_id != gui_thread_id
    assert snapshot.source_path == "source.png"
    assert project_dir == tmp_path
    assert received_block_id == block_id
    assert isinstance(kwargs["cancellation"], CancellationToken)
    assert completed == [expected]


def test_page_cleanup_worker_continues_after_one_block_fails(qapp, tmp_path: Path) -> None:
    page = Page(source_path="source.png", width=40, height=40)
    blocks = [
        TextBlock(
            page_id=page.id,
            bbox=BoundingBox(x=index * 10, y=0, width=8, height=8),
            reading_order=index,
        )
        for index in (1, 2)
    ]
    page.blocks = blocks

    class Service:
        def clean_block(self, page, project_dir, block_id, **kwargs):
            if block_id == blocks[0].id:
                raise RuntimeError("first failed")
            return tmp_path / "second.png"

    worker = PageImageCleanupWorker(
        Service(),
        page,
        tmp_path,
        (block.id for block in blocks),
    )
    progress = []
    completed = []
    worker.progress.connect(progress.append)
    worker.completed.connect(completed.append)

    thread = _start(worker)
    _wait(qapp, thread)

    assert [update.current for update in progress] == [1, 2]
    assert completed[0].paths == (tmp_path / "second.png",)
    assert completed[0].issues[0].block_id == blocks[0].id
    assert "first failed" in completed[0].issues[0].message


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
        watermark_logo_path=tmp_path / "logo.png",
        banner_path=tmp_path / "banner.png",
        banner_position="start",
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
    assert kwargs["watermark_text"] is None
    assert kwargs["watermark_logo_path"] == tmp_path / "logo.png"
    assert kwargs["banner_path"] == tmp_path / "banner.png"
    assert kwargs["banner_position"] == "start"
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


def test_preview_forwards_render_metrics_through_signal(qapp, tmp_path) -> None:
    page = Page(source_path="page.png", width=20, height=30)
    metric = RenderMetric(page.blocks[0].id if page.blocks else uuid4(), 24, (1, 2, 3), None)

    class Service:
        @classmethod
        def render_page_preview(cls, page, project_dir, destination, font_path, **kwargs):
            kwargs["on_metric"](metric)
            return (), ()

    worker = PreviewWorker(page, tmp_path, tmp_path / "preview.png", tmp_path / "font.ttf", Service)
    metrics = []
    worker.metric.connect(metrics.append)

    thread = _start(worker)
    _wait(qapp, thread)

    assert metrics == [metric]


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
