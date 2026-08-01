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

from PIL import (
    Image,
    ImageChops,
    ImageColor,
    ImageDraw,
    ImageFilter,
    ImageFont,
    ImageStat,
    UnidentifiedImageError,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from PySide6.QtCore import QTextBoundaryFinder

from app.core.coordinates import clamp_bbox
from app.core.language import resolve_source_language
from app.core.models import Page, Project, TextBlock
from app.services.workflow import CancellationToken, WorkflowCancelled

type ExportProgressCallback = Callable[["ExportProgress"], None]
type RgbColor = tuple[int, int, int]

_MIN_FONT_SIZE = 8
_MAX_FONT_SIZE = 256
_CLEANUP_RING_MARGIN = 5
_GRADIENT_CLEANUP_MARGIN = 4
_MANUAL_CLEANUP_MARGIN = 16
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
        clean_background: bool = False,
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
                    project_directory,
                    preview_path,
                    font_file,
                    color,
                    clean_background,
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

    @classmethod
    def render_page_preview(
        cls,
        page: Page,
        project_dir: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        font_path: str | os.PathLike[str],
        *,
        background_color: str = "white",
        clean_background: bool = False,
        cancellation: CancellationToken | None = None,
    ) -> tuple[tuple[OverflowWarning, ...], tuple[ExportIssue, ...]]:
        """Atomically render one page, returning ``(overflow_warnings, issues)``."""
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        project_directory = Path(project_dir).resolve()
        destination_path = Path(destination).resolve()
        font_file = Path(font_path).resolve()
        if destination_path.exists() and destination_path.is_dir():
            raise IsADirectoryError(f"preview destination is a directory: {destination_path}")
        color = cls._validate_inputs(destination_path.parent, font_file, background_color)
        source_path = cls._source_path(page, project_directory).resolve()
        cls._reject_source_collisions((source_path,), (destination_path,))
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        warnings, issues = cls._render_preview(
            page,
            cls._ordered_blocks(page.blocks),
            source_path,
            project_directory,
            destination_path,
            font_file,
            color,
            clean_background,
            token,
        )
        return tuple(warnings), tuple(issues)

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
        project_directory: Path,
        destination: Path,
        font_file: Path,
        background_color: RgbColor,
        clean_background: bool,
        token: CancellationToken,
    ) -> tuple[list[OverflowWarning], list[ExportIssue]]:
        try:
            with Image.open(source_path) as source:
                source.load()
                image = source.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"invalid source image {source_path}: {error}") from error

        original = image.copy()
        warnings: list[OverflowWarning] = []
        issues: list[ExportIssue] = []
        render_tasks: list[tuple[TextBlock, int, int, int, int]] = []
        stroke_blocks: set[UUID] = set()
        skipped_blocks: set[UUID] = set()
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
            render_tasks.append((block, left, top, right, bottom))

        for block, left, top, right, bottom in render_tasks:
            token.raise_if_cancelled()
            if clean_background:
                override_path = (
                    project_directory / "cleanups" / f"page-{page.id}" / f"block-{block.id}.png"
                )
                if override_path.exists():
                    cleanup_left = max(0, left - _MANUAL_CLEANUP_MARGIN)
                    cleanup_top = max(0, top - _MANUAL_CLEANUP_MARGIN)
                    cleanup_right = min(image.width, right + _MANUAL_CLEANUP_MARGIN)
                    cleanup_bottom = min(image.height, bottom + _MANUAL_CLEANUP_MARGIN)
                    cleanup_size = (cleanup_right - cleanup_left, cleanup_bottom - cleanup_top)
                    try:
                        with Image.open(override_path) as override_source:
                            if override_source.size != cleanup_size:
                                raise ValueError(
                                    f"expected {cleanup_size[0]}x{cleanup_size[1]}, "
                                    f"got {override_source.width}x{override_source.height}"
                                )
                            override_source.load()
                            override = override_source.convert("RGB")
                    except (
                        Image.DecompressionBombError,
                        UnidentifiedImageError,
                        OSError,
                        ValueError,
                    ) as error:
                        skipped_blocks.add(block.id)
                        issues.append(
                            ExportIssue(
                                message=f"invalid manual cleanup override: {error}",
                                page_id=page.id,
                                block_id=block.id,
                            )
                        )
                        continue
                    image.paste(override, (cleanup_left, cleanup_top))
                    stroke_blocks.add(block.id)
                    continue
                outer_left = max(0, left - _CLEANUP_RING_MARGIN)
                outer_top = max(0, top - _CLEANUP_RING_MARGIN)
                outer_right = min(image.width, right + _CLEANUP_RING_MARGIN)
                outer_bottom = min(image.height, bottom + _CLEANUP_RING_MARGIN)
                sample = original.crop((outer_left, outer_top, outer_right, outer_bottom))
                ring_mask = Image.new("L", sample.size, "white")
                ImageDraw.Draw(ring_mask).rectangle(
                    (
                        left - outer_left,
                        top - outer_top,
                        right - outer_left - 1,
                        bottom - outer_top - 1,
                    ),
                    fill="black",
                )
                sample_mask = ring_mask if ring_mask.getbbox() else None
                if sample_mask is not None:
                    ring_stats = ImageStat.Stat(sample, sample_mask)
                    ring_luminance = ImageStat.Stat(sample.convert("L"), sample_mask).mean[0]
                else:
                    crop = original.crop((left, top, right, bottom))
                    ring_stats = ImageStat.Stat(crop)
                    ring_luminance = ImageStat.Stat(crop.convert("L")).mean[0]
                background = tuple(ring_stats.median)
                deviation = max(ring_stats.stddev)
                detail = max(
                    ImageStat.Stat(
                        ImageChops.difference(
                            sample,
                            sample.filter(ImageFilter.GaussianBlur(2)),
                        ),
                        sample_mask,
                    ).mean
                )
                # ponytail: ring/color heuristics are cheap; preserve art when uncertain.
                flat = deviation <= 20 or (ring_luminance >= 210 and deviation <= 35)
                smooth_gradient = not flat and detail <= 2.1
                if not flat and not smooth_gradient:
                    skipped_blocks.add(block.id)
                    issues.append(
                        ExportIssue(
                            message=(
                                "background cleanup skipped: complex artwork requires "
                                "manual/context-aware cleanup"
                            ),
                            page_id=page.id,
                            block_id=block.id,
                        )
                    )
                    continue
                background_luminance = (
                    Image.new("RGB", (1, 1), background).convert("L").getpixel((0, 0))
                )
                clean_left, clean_top = left, top
                crop = original.crop((left, top, right, bottom))
                if smooth_gradient:
                    clean_left = left - _GRADIENT_CLEANUP_MARGIN
                    clean_top = top - _GRADIENT_CLEANUP_MARGIN
                    clean_right = right + _GRADIENT_CLEANUP_MARGIN
                    clean_bottom = bottom + _GRADIENT_CLEANUP_MARGIN
                    cleaned = (
                        cls._reconstruct_gradient(
                            original.crop((clean_left, clean_top, clean_right, clean_bottom)),
                            original.crop(
                                (
                                    clean_left - 8,
                                    clean_top,
                                    clean_left,
                                    clean_bottom,
                                )
                            ),
                            original.crop(
                                (
                                    clean_right,
                                    clean_top,
                                    clean_right + 8,
                                    clean_bottom,
                                )
                            ),
                        )
                        if (
                            clean_left >= 8
                            and clean_top >= 0
                            and clean_right + 8 <= original.width
                            and clean_bottom <= original.height
                        )
                        else None
                    )
                else:
                    cleaned = cls._inpaint_glyphs(crop, background)
                if smooth_gradient and cleaned is None:
                    skipped_blocks.add(block.id)
                    issues.append(
                        ExportIssue(
                            message=(
                                "background cleanup skipped: smooth-gradient context "
                                "was unavailable"
                            ),
                            page_id=page.id,
                            block_id=block.id,
                        )
                    )
                    continue
                if cleaned is None:
                    image.paste(background, (left, top, right, bottom))
                    if background_luminance < 145:
                        stroke_blocks.add(block.id)
                else:
                    image.paste(cleaned, (clean_left, clean_top))
                    if background_luminance < 145:
                        stroke_blocks.add(block.id)
            else:
                image.paste(background_color, (left, top, right, bottom))

        for block, left, top, right, bottom in render_tasks:
            token.raise_if_cancelled()
            if block.id in skipped_blocks:
                continue
            width, height = right - left, bottom - top
            rendered, overflow = cls._render_text_box(
                block.translated_text,
                width,
                height,
                font_file,
                image.crop((left, top, right, bottom)),
                add_stroke=block.id in stroke_blocks,
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

        for block, left, top, right, bottom in render_tasks:
            if block.id in skipped_blocks:
                image.paste(original.crop((left, top, right, bottom)), (left, top))

        token.raise_if_cancelled()
        cls._atomic_image(destination, image)
        return warnings, issues

    @staticmethod
    def _inpaint_glyphs(
        crop: Image.Image,
        background: RgbColor,
    ) -> Image.Image | None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None

        pixels = np.asarray(crop)
        distance = np.max(
            np.abs(pixels.astype(np.int16) - np.asarray(background, dtype=np.int16)),
            axis=2,
        )
        seeds = (distance >= 70).astype(np.uint8) * 255
        mask = ExportService._compact_glyph_mask(seeds, crop, expansion=9)
        if cv2.countNonZero(mask) < 4:
            return None
        return Image.fromarray(cv2.inpaint(pixels, mask, 3, cv2.INPAINT_TELEA))

    @staticmethod
    def _reconstruct_gradient(
        crop: Image.Image,
        left_strip: Image.Image,
        right_strip: Image.Image,
    ) -> Image.Image | None:
        try:
            import numpy as np
        except ImportError:
            return None

        if left_strip.size != (8, crop.height) or right_strip.size != (8, crop.height):
            return None
        if any(
            max(
                ImageStat.Stat(
                    ImageChops.difference(strip, strip.filter(ImageFilter.GaussianBlur(2)))
                ).mean
            )
            > 2.1
            for strip in (left_strip, right_strip)
        ):
            return None
        left = np.median(np.asarray(left_strip), axis=1)
        right = np.median(np.asarray(right_strip), axis=1)
        blend = ((np.arange(crop.width) + 4.5) / (crop.width + 8))[None, :, None]
        model = left[:, None, :] * (1 - blend) + right[:, None, :] * blend
        return Image.fromarray(np.clip(model, 0, 255).astype(np.uint8))

    @staticmethod
    def _compact_glyph_mask(seeds, crop: Image.Image, *, expansion: int):
        import cv2
        import numpy as np

        count, labels, stats, _ = cv2.connectedComponentsWithStats(seeds, connectivity=8)
        mask = np.zeros(crop.size[::-1], dtype=np.uint8)
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            if (
                area < 4
                or area > mask.size // 4
                or width * 100 >= crop.width * 55
                or height * 100 >= crop.height * 55
                or x == 0
                or y == 0
                or x + width == crop.width
                or y + height == crop.height
            ):
                continue
            mask[labels == label] = 255
        kernel_size = expansion * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
        mask = cv2.dilate(mask, kernel, iterations=1)
        return mask

    @classmethod
    def _render_text_box(
        cls,
        text: str,
        width: int,
        height: int,
        font_file: Path,
        background: RgbColor | Image.Image,
        *,
        add_stroke: bool = False,
    ) -> tuple[Image.Image, bool]:
        canvas = (
            background.copy().convert("RGB")
            if isinstance(background, Image.Image)
            else Image.new("RGB", (width, height), background)
        )
        draw = ImageDraw.Draw(canvas)
        luminance = ImageStat.Stat(canvas.convert("L")).mean[0]
        fill = "black" if luminance >= 145 else "white"
        stroke_fill = "white" if fill == "black" else "black"
        padding = max(1, min(width, height) // 10)
        available_width = max(1, width - 2 * padding)
        available_height = max(1, height - 2 * padding)
        selected: (
            tuple[ImageFont.FreeTypeFont, list[str], int, int, tuple[int, int, int, int]] | None
        ) = None
        maximum_size = min(_MAX_FONT_SIZE, max(_MIN_FONT_SIZE, available_height))
        for size in range(maximum_size, _MIN_FONT_SIZE - 1, -1):
            font = ImageFont.truetype(font_file, size)
            lines = cls._wrap_text(draw, text, font, available_width)
            spacing = max(1, size // 5)
            stroke_width = max(1, size // 18) if add_stroke else 0
            bounds = draw.multiline_textbbox(
                (0, 0),
                "\n".join(lines),
                font=font,
                spacing=spacing,
                stroke_width=stroke_width,
                align="center",
            )
            selected = font, lines, spacing, stroke_width, bounds
            if (
                bounds[2] - bounds[0] <= available_width
                and bounds[3] - bounds[1] <= available_height
            ):
                break

        assert selected is not None
        font, lines, spacing, stroke_width, bounds = selected
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        overflow = text_width > available_width or text_height > available_height
        draw.multiline_text(
            ((width - text_width) / 2 - bounds[0], (height - text_height) / 2 - bounds[1]),
            "\n".join(lines),
            fill=fill,
            font=font,
            spacing=spacing,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
            align="center",
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
