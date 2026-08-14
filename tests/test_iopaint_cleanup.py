import threading
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from app.core.models import BoundingBox, Page, TextBlock
from app.services.export import ExportService
from app.services.iopaint_cleanup import (
    IOPaintCleanupService,
    IOPaintConfiguration,
    _default_iopaint_executable,
)
from app.services.workflow import CancellationToken, WorkflowCancelled


def _page(tmp_path: Path) -> tuple[Page, TextBlock]:
    source = tmp_path / "page.png"
    Image.new("RGB", (80, 80), "blue").save(source)
    page = Page(source_path=str(source), width=80, height=80)
    block = TextBlock(
        id=uuid4(),
        page_id=page.id,
        bbox=BoundingBox(x=20, y=25, width=30, height=16),
        reading_order=1,
        rotation_degrees=35,
    )
    page.blocks = [block]
    return page, block


class _FinishedProcess:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode


def test_configuration_validates_runtime_values() -> None:
    assert IOPaintConfiguration().operation_timeout_seconds == 300
    for options in (
        {"executable": ""},
        {"model": ""},
        {"device": ""},
        {"operation_timeout_seconds": 0},
    ):
        with pytest.raises(ValueError):
            IOPaintConfiguration(**options)


def test_default_executable_discovers_local_install(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "Apps/iopaint/venv/bin/iopaint"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="ascii")
    executable.chmod(0o755)
    monkeypatch.setattr("app.services.iopaint_cleanup.shutil.which", lambda _name: None)
    monkeypatch.setattr("app.services.iopaint_cleanup.Path.home", lambda: tmp_path)

    assert _default_iopaint_executable() == str(executable)


def test_cleanup_runs_cli_with_rotated_mask_and_preserves_existing_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    page, block = _page(tmp_path)
    commands = []
    masks = []

    def run_iopaint(command, **_kwargs):
        commands.append(command)
        image_path = Path(command[5])
        mask_path = Path(command[7])
        output_path = Path(command[9]) / image_path.name
        output_path.parent.mkdir()
        with Image.open(image_path) as image, Image.open(mask_path) as mask:
            masks.append(mask.convert("L").copy())
            Image.new("RGB", image.size, "red").save(output_path)
        return _FinishedProcess()

    monkeypatch.setattr("app.services.iopaint_cleanup.subprocess.Popen", run_iopaint)
    service = IOPaintCleanupService(IOPaintConfiguration(executable="/venv/bin/iopaint"))

    path = service.clean_block(page, tmp_path, block.id)

    command = commands[0]
    assert command[:5] == [
        "/venv/bin/iopaint",
        "run",
        "--model=lama",
        "--device=cpu",
        "--image",
    ]
    assert command[6] == "--mask"
    assert command[8] == "--output"
    assert Path(command[9]).name == "output"
    with Image.open(path) as cleanup:
        assert cleanup.getpixel((0, 0)) == (0, 0, 255)
        masked = masks[0].getbbox()
        assert masked is not None
        center = ((masked[0] + masked[2]) // 2, (masked[1] + masked[3]) // 2)
        assert cleanup.getpixel(center) == (255, 0, 0)

    service.clean_block(page, tmp_path, block.id)
    assert path.with_name(f"{path.name}.bak").is_file()


@pytest.mark.parametrize("output_kind", ["missing", "invalid", "wrong-size"])
def test_cleanup_rejects_unusable_cli_output(
    tmp_path: Path,
    monkeypatch,
    output_kind: str,
) -> None:
    page, block = _page(tmp_path)

    def run_iopaint(command, **_kwargs):
        output_path = Path(command[9]) / Path(command[5]).name
        if output_kind == "invalid":
            output_path.parent.mkdir()
            output_path.write_bytes(b"not an image")
        elif output_kind == "wrong-size":
            output_path.parent.mkdir()
            Image.new("RGB", (1, 1)).save(output_path)
        return _FinishedProcess()

    monkeypatch.setattr("app.services.iopaint_cleanup.subprocess.Popen", run_iopaint)

    with pytest.raises(RuntimeError, match="no output|invalid output|expected"):
        IOPaintCleanupService(IOPaintConfiguration()).clean_block(page, tmp_path, block.id)


def test_cli_failure_preserves_existing_cleanup(tmp_path: Path, monkeypatch) -> None:
    page, block = _page(tmp_path)
    path = ExportService._manual_cleanup_path(tmp_path, page, block)
    path.parent.mkdir(parents=True)
    Image.new("RGB", (62, 48), "green").save(path)

    def fail_iopaint(_command, **kwargs):
        kwargs["stdout"].write(b"model failed")
        return _FinishedProcess(2)

    monkeypatch.setattr("app.services.iopaint_cleanup.subprocess.Popen", fail_iopaint)

    with pytest.raises(RuntimeError, match="code 2: model failed"):
        IOPaintCleanupService(IOPaintConfiguration()).clean_block(page, tmp_path, block.id)

    with Image.open(path) as cleanup:
        assert cleanup.getpixel((0, 0)) == (0, 128, 0)
    assert not path.with_name(f"{path.name}.bak").exists()


def test_cleanup_cancellation_terminates_cli(monkeypatch) -> None:
    started = threading.Event()
    cancellation = CancellationToken()

    class BlockingProcess:
        returncode = None
        terminated = False

        def poll(self):
            started.set()
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = BlockingProcess()
    monkeypatch.setattr(
        "app.services.iopaint_cleanup.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    errors = []

    def run() -> None:
        try:
            IOPaintCleanupService(IOPaintConfiguration())._inpaint(
                Image.new("RGB", (6, 4)),
                Image.new("L", (6, 4), 255),
                cancellation,
            )
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(1)
    cancellation.cancel()
    thread.join(1)

    assert not thread.is_alive()
    assert process.terminated
    assert isinstance(errors[0], WorkflowCancelled)


def test_cleanup_timeout_terminates_cli(monkeypatch) -> None:
    class BlockingProcess:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout):
            return self.returncode

    process = BlockingProcess()
    monkeypatch.setattr(
        "app.services.iopaint_cleanup.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    service = IOPaintCleanupService(IOPaintConfiguration(operation_timeout_seconds=0.01))

    with pytest.raises(TimeoutError, match="timed out"):
        service._inpaint(
            Image.new("RGB", (6, 4)),
            Image.new("L", (6, 4), 255),
            CancellationToken(),
        )

    assert process.terminated
