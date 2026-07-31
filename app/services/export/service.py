"""Synchronous, worker-thread-ready project export."""

import csv
import io
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated
from uuid import UUID

from PIL import Image, ImageColor, ImageDraw, ImageFont, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from PySide6.QtCore import QTextBoundaryFinder

from app.core.coordinates import clamp_bbox
from app.core.language import resolve_source_language
from app.core.models import Page, Project, TextBlock
from app.services.workflow import CancellationToken, WorkflowCancelled

type ExportProgressCallback = Callable[["ExportProgress"], None]
type RgbColor = tuple[int, int, int]

_MIN_FONT_SIZE = 8
_MAX_FONT_SIZE = 48
_STRUCTURED_FILENAMES = (
    "project-export.json",
    "project-export.csv",
    "project-export.txt",
)
_CSV_COLUMNS = (
    "page_index",
    "page_id",
    "page_path",
    "block_id",
    "reading_order",
    "source_language",
    "ocr_provider",
    "speaker",
    "source",
    "translation",
    "note",
    "status",
    "writing_mode",
    "bbox_x",
    "bbox_y",
    "bbox_width",
    "bbox_height",
)


class _ExportValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExportProgress(_ExportValue):
    current: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    message: str
    page_id: UUID | None = None

    @model_validator(mode="after")
    def validate_progress(self) -> "ExportProgress":
        if self.current > self.total:
            raise ValueError("current cannot exceed total")
        return self


class ExportIssue(_ExportValue):
    message: str
    page_id: UUID | None = None
    block_id: UUID | None = None
    recoverable: bool = True


class OverflowWarning(_ExportValue):
    message: str
    page_id: UUID
    block_id: UUID


class ExportResult(_ExportValue):
    json_path: Path
    csv_path: Path
    txt_path: Path
    preview_paths: tuple[Path, ...] = ()
    issues: tuple[ExportIssue, ...] = ()
    overflow_warnings: tuple[OverflowWarning, ...] = ()


class ExportService:
    @classmethod
    def export(
        cls,
        project: Project,
        project_dir: str | os.PathLike[str],
        output_dir: str | os.PathLike[str],
        font_path: str | os.PathLike[str],
        *,
        background_color: str = "white",
        cancellation: CancellationToken | None = None,
        on_progress: ExportProgressCallback | None = None,
    ) -> ExportResult:
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()

        project_directory = Path(project_dir).resolve()
        output_directory = Path(output_dir).resolve()
        font_file = Path(font_path).resolve()
        color = cls._validate_inputs(output_directory, font_file, background_color)
        source_paths = [
            cls._source_path(page, project_directory).resolve() for page in project.pages
        ]
        preview_paths = [
            output_directory / f"preview-page-{index:04d}-{page.id}.png"
            for index, page in enumerate(project.pages, 1)
        ]
        destinations = [
            *(output_directory / name for name in _STRUCTURED_FILENAMES),
            *preview_paths,
        ]
        cls._reject_source_collisions(source_paths, destinations)
        output_directory.mkdir(parents=True, exist_ok=True)

        json_path, csv_path, txt_path = destinations[:3]
        ordered = [
            (page_index, page, cls._ordered_blocks(page.blocks))
            for page_index, page in enumerate(project.pages, 1)
        ]
        token.raise_if_cancelled()
        cls._atomic_text(
            json_path,
            json.dumps(project.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        )
        token.raise_if_cancelled()
        cls._atomic_text(csv_path, cls._csv_text(project, ordered, token))
        token.raise_if_cancelled()
        cls._atomic_text(txt_path, cls._txt_text(ordered, token))

        written_previews: list[Path] = []
        issues: list[ExportIssue] = []
        warnings: list[OverflowWarning] = []
        total = len(project.pages)
        for current, ((_, page, blocks), preview_path) in enumerate(
            zip(ordered, preview_paths, strict=True),
            1,
        ):
            token.raise_if_cancelled()
            try:
                page_warnings, page_issues = cls._render_preview(
                    page,
                    blocks,
                    cls._source_path(page, project_directory),
                    preview_path,
                    font_file,
                    color,
                    token,
                )
                warnings.extend(page_warnings)
                issues.extend(page_issues)
                written_previews.append(preview_path)
                message = "Preview exported"
            except WorkflowCancelled:
                raise
            except Exception as error:
                issues.append(
                    ExportIssue(
                        message=f"preview export failed: {error}",
                        page_id=page.id,
                    )
                )
                message = "Preview failed; page skipped"
            if on_progress is not None:
                on_progress(
                    ExportProgress(
                        current=current,
                        total=total,
                        message=message,
                        page_id=page.id,
                    )
                )

        token.raise_if_cancelled()
        return ExportResult(
            json_path=json_path,
            csv_path=csv_path,
            txt_path=txt_path,
            preview_paths=tuple(written_previews),
            issues=tuple(issues),
            overflow_warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_inputs(
        output_directory: Path,
        font_file: Path,
        background_color: str,
    ) -> RgbColor:
        if output_directory.exists() and not output_directory.is_dir():
            raise NotADirectoryError(f"output path is not a directory: {output_directory}")
        if not font_file.is_file():
            raise FileNotFoundError(f"font not found: {font_file}")
        try:
            ImageFont.truetype(font_file, _MIN_FONT_SIZE)
        except OSError as error:
            raise ValueError(f"invalid TrueType/OpenType font: {font_file}") from error
        try:
            return ImageColor.getcolor(background_color, "RGB")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid background color: {background_color!r}") from error

    @staticmethod
    def _reject_source_collisions(
        sources: Iterable[Path],
        destinations: Iterable[Path],
    ) -> None:
        resolved_sources = set(sources)
        for destination in destinations:
            if destination.resolve() in resolved_sources:
                raise ValueError(
                    f"export destination would overwrite a source image: {destination}"
                )

    @staticmethod
    def _source_path(page: Page, project_directory: Path) -> Path:
        path = Path(page.source_path)
        return path if path.is_absolute() else project_directory / path

    @staticmethod
    def _ordered_blocks(blocks: Iterable[TextBlock]) -> list[TextBlock]:
        return sorted(blocks, key=lambda block: (block.reading_order, str(block.id)))

    @staticmethod
    def _atomic_text(destination: Path, content: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=destination.parent,
                prefix=f".{destination.name}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _csv_text(
        project: Project,
        pages: Iterable[tuple[int, Page, list[TextBlock]]],
        token: CancellationToken,
    ) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for page_index, page, blocks in pages:
            token.raise_if_cancelled()
            for block in blocks:
                token.raise_if_cancelled()
                writer.writerow(
                    {
                        "page_index": page_index,
                        "page_id": str(page.id),
                        "page_path": page.source_path,
                        "block_id": str(block.id),
                        "reading_order": block.reading_order,
                        "source_language": resolve_source_language(
                            project.settings, page, block
                        ).value,
                        "ocr_provider": block.ocr_provider or "",
                        "speaker": block.speaker,
                        "source": block.source_text,
                        "translation": block.translated_text,
                        "note": block.note,
                        "status": block.status.value,
                        "writing_mode": block.writing_mode.value,
                        "bbox_x": block.bbox.x,
                        "bbox_y": block.bbox.y,
                        "bbox_width": block.bbox.width,
                        "bbox_height": block.bbox.height,
                    }
                )
        return output.getvalue()

    @staticmethod
    def _txt_text(
        pages: Iterable[tuple[int, Page, list[TextBlock]]],
        token: CancellationToken,
    ) -> str:
        lines: list[str] = []
        for page_index, page, blocks in pages:
            token.raise_if_cancelled()
            lines.append(f"Page {page_index}: {page.source_path}")
            for block in blocks:
                token.raise_if_cancelled()
                lines.extend(
                    (
                        f"[{block.reading_order}] Source: {block.source_text}",
                        f"[{block.reading_order}] Thai: {block.translated_text}",
                    )
                )
            lines.append("")
        return "\n".join(lines)

    @classmethod
    def _render_preview(
        cls,
        page: Page,
        blocks: Iterable[TextBlock],
        source_path: Path,
        destination: Path,
        font_file: Path,
        background_color: RgbColor,
        token: CancellationToken,
    ) -> tuple[list[OverflowWarning], list[ExportIssue]]:
        try:
            with Image.open(source_path) as source:
                source.load()
                image = source.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"invalid source image {source_path}: {error}") from error

        warnings: list[OverflowWarning] = []
        issues: list[ExportIssue] = []
        for block in blocks:
            token.raise_if_cancelled()
            try:
                bbox = clamp_bbox(block.bbox, *image.size)
            except ValueError as error:
                issues.append(
                    ExportIssue(
                        message=f"invalid bounding box: {error}",
                        page_id=page.id,
                        block_id=block.id,
                    )
                )
                continue
            if not block.translated_text:
                continue

            left = max(0, int(bbox.x))
            top = max(0, int(bbox.y))
            right = min(image.width, max(left + 1, int(bbox.x + bbox.width + 0.999)))
            bottom = min(image.height, max(top + 1, int(bbox.y + bbox.height + 0.999)))
            width, height = right - left, bottom - top
            rendered, overflow = cls._render_text_box(
                block.translated_text,
                width,
                height,
                font_file,
                background_color,
            )
            image.paste(rendered, (left, top))
            if overflow:
                ImageDraw.Draw(image).rectangle(
                    (left, top, right - 1, bottom - 1),
                    outline="red",
                    width=min(3, width, height),
                )
                warnings.append(
                    OverflowWarning(
                        message="Thai translation does not fit at minimum font size",
                        page_id=page.id,
                        block_id=block.id,
                    )
                )

        token.raise_if_cancelled()
        cls._atomic_image(destination, image)
        return warnings, issues

    @classmethod
    def _render_text_box(
        cls,
        text: str,
        width: int,
        height: int,
        font_file: Path,
        background_color: RgbColor,
    ) -> tuple[Image.Image, bool]:
        canvas = Image.new("RGB", (width, height), background_color)
        draw = ImageDraw.Draw(canvas)
        selected: (
            tuple[ImageFont.FreeTypeFont, list[str], int, tuple[int, int, int, int]] | None
        ) = None
        for size in range(min(_MAX_FONT_SIZE, max(_MIN_FONT_SIZE, height)), _MIN_FONT_SIZE - 1, -1):
            font = ImageFont.truetype(font_file, size)
            lines = cls._wrap_text(draw, text, font, width)
            spacing = max(1, size // 5)
            bounds = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=spacing)
            selected = font, lines, spacing, bounds
            if bounds[2] - bounds[0] <= width and bounds[3] - bounds[1] <= height:
                break

        assert selected is not None
        font, lines, spacing, bounds = selected
        overflow = bounds[2] - bounds[0] > width or bounds[3] - bounds[1] > height
        draw.multiline_text(
            (-bounds[0], -bounds[1]),
            "\n".join(lines),
            fill="black",
            font=font,
            spacing=spacing,
        )
        return canvas, overflow

    @classmethod
    def _wrap_text(
        cls,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        width: int,
    ) -> list[str]:
        lines: list[str] = []
        for paragraph in text.split("\n"):
            clusters = cls._graphemes(paragraph)
            if not clusters:
                lines.append("")
                continue
            while clusters:
                fit = 0
                for end in range(1, len(clusters) + 1):
                    if draw.textlength("".join(clusters[:end]), font=font) > width:
                        break
                    fit = end
                if fit == len(clusters):
                    lines.append("".join(clusters))
                    break
                if fit == 0:
                    fit = 1
                whitespace = max(
                    (index for index in range(1, fit + 1) if clusters[index - 1].isspace()),
                    default=0,
                )
                consume = whitespace or fit
                lines.append("".join(clusters[:consume]).rstrip())
                clusters = clusters[consume:]
                while clusters and clusters[0].isspace():
                    clusters.pop(0)
        return lines

    @staticmethod
    def _graphemes(text: str) -> list[str]:
        if not text:
            return []
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        utf16_offsets = [0]
        for character in text:
            utf16_offsets.append(utf16_offsets[-1] + len(character.encode("utf-16-le")) // 2)
        python_indexes = {offset: index for index, offset in enumerate(utf16_offsets)}
        boundaries = [0]
        while (boundary := finder.toNextBoundary()) != -1:
            boundaries.append(python_indexes[boundary])
        return [text[start:end] for start, end in zip(boundaries[:-1], boundaries[1:], strict=True)]

    @staticmethod
    def _atomic_image(destination: Path, image: Image.Image) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                dir=destination.parent,
                prefix=f".{destination.name}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                image.save(temporary_file, format="PNG")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
        except BaseException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
