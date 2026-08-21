"""Local IOPaint cleanup through one CLI process per operation."""

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from PIL import Image, UnidentifiedImageError

from app.core.models import Page
from app.services.export import ExportService
from app.services.workflow import CancellationToken


def _default_iopaint_executable() -> str:
    if executable := shutil.which("iopaint"):
        return executable
    local_install = Path.home() / "Apps/iopaint/venv/bin/iopaint"
    if local_install.is_file() and os.access(local_install, os.X_OK):
        return str(local_install)
    return "iopaint"


@dataclass(frozen=True, slots=True)
class IOPaintConfiguration:
    executable: str = field(default_factory=_default_iopaint_executable)
    model: str = "lama"
    device: str = "cpu"
    operation_timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("IOPaint executable is missing")
        if not self.model.strip():
            raise ValueError("IOPaint model is missing")
        if not self.device.strip():
            raise ValueError("IOPaint device is missing")
        if self.operation_timeout_seconds <= 0:
            raise ValueError("IOPaint operation timeout must be positive")


class IOPaintCleanupService:
    """Create validated cleanup overrides through the local IOPaint CLI."""

    def __init__(self, configuration: IOPaintConfiguration) -> None:
        self._configuration = configuration

    def clean_block(
        self,
        page: Page,
        project_dir: str | Path,
        block_id: UUID,
        *,
        cancellation: CancellationToken | None = None,
    ) -> Path:
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        block = next((item for item in page.blocks if item.id == block_id), None)
        if block is None:
            raise ValueError(f"unknown cleanup block ID: {block_id}")
        project_directory = Path(project_dir).resolve()
        source_path = ExportService._source_path(page, project_directory).resolve()
        try:
            with Image.open(source_path) as source:
                source.load()
                image = source.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"invalid source image {source_path}: {error}") from error

        bounds = ExportService._block_bounds(block, image.size)
        cleanup_bounds = ExportService._manual_cleanup_bounds(bounds, image.size)
        crop = image.crop(cleanup_bounds)
        mask = ExportService._region_mask(block, cleanup_bounds)
        output = self._inpaint(crop, mask, token)
        token.raise_if_cancelled()
        safe_output = crop.copy()
        safe_output.paste(output, mask=mask)

        ExportService.archive_manual_cleanups(project_directory, page.id, (block.id,))
        cleanup_path = ExportService._manual_cleanup_path(project_directory, page, block)
        cleanup_path.parent.mkdir(parents=True, exist_ok=True)
        ExportService._atomic_image(cleanup_path, safe_output)
        return cleanup_path

    def _inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        token: CancellationToken,
    ) -> Image.Image:
        token.raise_if_cancelled()
        with tempfile.TemporaryDirectory(prefix="manga-iopaint-") as temporary_dir:
            directory = Path(temporary_dir)
            image_path = directory / "image.png"
            mask_path = directory / "mask.png"
            output_directory = directory / "output"
            output_path = output_directory / image_path.name
            image.convert("RGB").save(image_path, format="PNG")
            mask.convert("L").save(mask_path, format="PNG")
            command = [
                self._configuration.executable,
                "run",
                f"--model={self._configuration.model}",
                f"--device={self._configuration.device}",
                "--image",
                str(image_path),
                "--mask",
                str(mask_path),
                "--output",
                str(output_directory),
            ]
            with tempfile.TemporaryFile() as log:
                try:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                except OSError as error:
                    raise RuntimeError(f"could not run IOPaint: {error}") from error
                deadline = time.monotonic() + self._configuration.operation_timeout_seconds
                try:
                    while process.poll() is None:
                        token.raise_if_cancelled()
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "IOPaint cleanup timed out after "
                                f"{self._configuration.operation_timeout_seconds:g} seconds"
                            )
                        time.sleep(0.05)
                    token.raise_if_cancelled()
                except BaseException:
                    _stop_process(process)
                    raise
                if process.returncode != 0:
                    details = _log_tail(log)
                    suffix = f": {details}" if details else ""
                    raise RuntimeError(f"IOPaint exited with code {process.returncode}{suffix}")

            if not output_path.is_file():
                raise RuntimeError("IOPaint produced no output image")
            try:
                with Image.open(output_path) as result:
                    result.load()
                    output = result.convert("RGB")
            except (UnidentifiedImageError, OSError) as error:
                raise RuntimeError("IOPaint produced an invalid output image") from error
            if output.size != image.size:
                raise RuntimeError(
                    f"IOPaint returned {output.width}x{output.height}; "
                    f"expected {image.width}x{image.height}"
                )
            return output


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _log_tail(log) -> str:
    log.flush()
    log.seek(0, 2)
    end = log.tell()
    log.seek(max(0, end - 2000))
    return log.read().decode("utf-8", errors="replace").strip()
