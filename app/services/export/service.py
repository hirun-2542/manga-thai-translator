"""Synchronous, worker-thread-ready project export."""

import csv
import io
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
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

from app.core.coordinates import clamp_bbox, rotated_bbox_bounds, rotated_bbox_corners
from app.core.language import resolve_source_language
from app.core.models import Page, Project, TextAlignment, TextBlock
from app.services.system_fonts import (
    FontResolutionError,
    SystemFont,
    load_pillow_font,
    resolve_system_font,
)
from app.services.workflow import CancellationToken, WorkflowCancelled

type ExportProgressCallback = Callable[["ExportProgress"], None]
type RenderMetricCallback = Callable[["RenderMetric"], None]
type RgbColor = tuple[int, int, int]
type BannerPosition = Literal["start", "end"]
type SourceTextStyle = tuple[RgbColor, RgbColor | None, int, int]
type TextMetricCallback = Callable[[int, RgbColor, RgbColor | None, int], None]

_MIN_FONT_SIZE = 8
_MAX_FONT_SIZE = 256
_CLEANUP_RING_MARGIN = 5
_GRADIENT_CLEANUP_MARGIN = 4
_MANUAL_CLEANUP_MARGIN = 16
_WATERMARK_MAX_LOGO_RATIO = 0.2
_WATERMARK_MAX_TEXT_RATIO = 0.45
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
    "text_alignment",
    "bbox_x",
    "bbox_y",
    "bbox_width",
    "bbox_height",
    "rotation_degrees",
    "mirror_horizontal",
    "mirror_vertical",
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


@dataclass(frozen=True, slots=True)
class RenderMetric:
    """Transient values selected while rendering one translated block."""

    block_id: UUID
    font_size_px: int
    fill_rgb: RgbColor
    stroke_rgb: RgbColor | None
    font_label: str = ""
    stroke_width_px: int = 0


class ExportResult(_ExportValue):
    json_path: Path
    csv_path: Path
    txt_path: Path
    preview_paths: tuple[Path, ...] = ()
    issues: tuple[ExportIssue, ...] = ()
    overflow_warnings: tuple[OverflowWarning, ...] = ()


class ExportService:
    @staticmethod
    def _cleanup_label(page: Page) -> str:
        label = Path(page.source_path).stem
        safe = "".join(
            character if character.isalnum() or character in "._-" else "_" for character in label
        )
        return safe.strip("._") or "page"

    @classmethod
    def _manual_cleanup_path(
        cls,
        project_directory: Path,
        page: Page,
        block: TextBlock,
    ) -> Path:
        page_directory = (
            project_directory / "cleanups" / f"page-{cls._cleanup_label(page)}-{page.id}"
        )
        return page_directory / f"block-{block.reading_order:03d}-{block.id}.png"

    @classmethod
    def _manual_cleanup_candidates(
        cls,
        project_directory: Path,
        page_id: UUID,
        block_id: UUID,
        readable_path: Path | None = None,
    ) -> tuple[Path, ...]:
        cleanup_root = project_directory / "cleanups"
        candidates: list[Path] = []
        if readable_path is not None:
            candidates.append(readable_path)
        candidates.append(cleanup_root / f"page-{page_id}" / f"block-{block_id}.png")
        candidates.extend(
            sorted(
                cleanup_root.glob(f"page-*-{page_id}/block-*-{block_id}.png"),
                key=lambda path: str(path),
            )
        )
        return tuple(dict.fromkeys(candidates))

    @classmethod
    def _find_manual_cleanup(
        cls,
        project_directory: Path,
        page: Page,
        block: TextBlock,
    ) -> Path | None:
        readable_path = cls._manual_cleanup_path(project_directory, page, block)
        return next(
            (
                path
                for path in cls._manual_cleanup_candidates(
                    project_directory,
                    page.id,
                    block.id,
                    readable_path,
                )
                if path.exists()
            ),
            None,
        )

    @staticmethod
    def archive_manual_cleanups(
        project_dir: str | os.PathLike[str],
        page_id: UUID,
        block_ids: Iterable[UUID],
    ) -> tuple[Path, ...]:
        """Archive matching cleanup overrides so local cleanup can run again."""
        project_directory = Path(project_dir).resolve()
        archived: list[Path] = []
        for block_id in dict.fromkeys(block_ids):
            for override_path in ExportService._manual_cleanup_candidates(
                project_directory, page_id, block_id
            ):
                if not override_path.exists():
                    continue
                if not override_path.is_file():
                    raise IsADirectoryError(f"cleanup override is not a file: {override_path}")
                backup_path = override_path.with_name(f"{override_path.name}.bak")
                counter = 2
                while backup_path.exists():
                    backup_path = override_path.with_name(f"{override_path.name}.bak{counter}")
                    counter += 1
                override_path.rename(backup_path)
                archived.append(backup_path)
        return tuple(archived)

    @classmethod
    def prepare_manual_cleanups(
        cls,
        page: Page,
        project_dir: str | os.PathLike[str],
        block_ids: Iterable[UUID],
    ) -> tuple[Path, ...]:
        """Create exact-size source crops for manual cleanup without overwriting edits."""
        requested_ids = tuple(dict.fromkeys(block_ids))
        if not requested_ids:
            return ()
        blocks = {block.id: block for block in page.blocks}
        unknown_ids = [block_id for block_id in requested_ids if block_id not in blocks]
        if unknown_ids:
            raise ValueError(f"unknown cleanup block ID(s): {len(unknown_ids)}")

        project_directory = Path(project_dir).resolve()
        source_path = cls._source_path(page, project_directory).resolve()
        try:
            with Image.open(source_path) as source:
                source.load()
                image = source.convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"invalid source image {source_path}: {error}") from error

        paths: list[Path] = []
        for block_id in requested_ids:
            block = blocks[block_id]
            bounds = cls._block_bounds(block, image.size)
            cleanup_box = cls._manual_cleanup_bounds(bounds, image.size)
            existing_path = cls._find_manual_cleanup(project_directory, page, block)
            cleanup_path = existing_path or cls._manual_cleanup_path(project_directory, page, block)
            if cleanup_path.exists():
                if not cleanup_path.is_file():
                    raise IsADirectoryError(f"cleanup override is not a file: {cleanup_path}")
            else:
                cleanup_path.parent.mkdir(parents=True, exist_ok=True)
                cls._atomic_image(cleanup_path, image.crop(cleanup_box))
            paths.append(cleanup_path)
        return tuple(paths)

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
        watermark_text: str | None = None,
        watermark_logo_path: str | os.PathLike[str] | None = None,
        banner_path: str | os.PathLike[str] | None = None,
        banner_position: BannerPosition = "end",
        cancellation: CancellationToken | None = None,
        on_progress: ExportProgressCallback | None = None,
    ) -> ExportResult:
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()

        project_directory = Path(project_dir).resolve()
        output_directory = Path(output_dir).resolve()
        font_file = Path(font_path).resolve()
        color = cls._validate_inputs(output_directory, font_file, background_color)
        watermark_text, logo_source, logo = cls._load_watermark(
            watermark_text,
            watermark_logo_path,
        )
        banner_source, banner = cls._load_export_image(banner_path, "banner")
        if banner_position not in ("start", "end"):
            raise ValueError(f"invalid banner position: {banner_position!r}")
        source_paths = [
            cls._source_path(page, project_directory).resolve() for page in project.pages
        ]
        source_paths.extend(path for path in (logo_source, banner_source) if path is not None)
        page_offset = 1 if banner is not None and banner_position == "start" else 0
        preview_paths = [
            output_directory / f"{index + page_offset}.png"
            for index, _page in enumerate(project.pages, 1)
        ]
        banner_destination = (
            output_directory
            / ("1.png" if banner_position == "start" else f"{len(project.pages) + 1}.png")
            if banner is not None
            else None
        )
        output_images = [
            *([banner_destination] if banner_destination is not None and page_offset else []),
            *preview_paths,
            *([banner_destination] if banner_destination is not None and not page_offset else []),
        ]
        destinations = [
            *(output_directory / name for name in _STRUCTURED_FILENAMES),
            *output_images,
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
        total = len(project.pages) + (1 if banner is not None else 0)
        current = 0
        if banner is not None and banner_destination is not None and page_offset:
            token.raise_if_cancelled()
            cls._atomic_image(banner_destination, banner)
            written_previews.append(banner_destination)
            current += 1
            if on_progress is not None:
                on_progress(
                    ExportProgress(
                        current=current,
                        total=total,
                        message="Banner exported",
                    )
                )
        for (_, page, blocks), preview_path in zip(ordered, preview_paths, strict=True):
            current += 1
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
                    watermark_text=watermark_text,
                    watermark_logo=logo,
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
        if banner is not None and banner_destination is not None and not page_offset:
            token.raise_if_cancelled()
            cls._atomic_image(banner_destination, banner)
            written_previews.append(banner_destination)
            current += 1
            if on_progress is not None:
                on_progress(
                    ExportProgress(
                        current=current,
                        total=total,
                        message="Banner exported",
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
        on_metric: RenderMetricCallback | None = None,
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
            on_metric,
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
            load_pillow_font(
                SystemFont(
                    family="",
                    style="",
                    path=font_file,
                    display_label=font_file.name,
                ),
                _MIN_FONT_SIZE,
            )
        except OSError as error:
            raise ValueError(f"invalid TrueType/OpenType font: {font_file}") from error
        try:
            return ImageColor.getcolor(background_color, "RGB")
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid background color: {background_color!r}") from error

    @staticmethod
    def _global_font(font_file: Path) -> SystemFont:
        return SystemFont(
            family="",
            style="",
            path=font_file,
            display_label=font_file.name,
        )

    @staticmethod
    def _parse_rgb(value: str | None) -> RgbColor | None:
        if value is None:
            return None
        color = ImageColor.getcolor(value, "RGB")
        return tuple(int(channel) for channel in color)

    @staticmethod
    def _default_stroke_color(fill: RgbColor) -> RgbColor:
        return (255, 255, 255) if fill == (0, 0, 0) else (0, 0, 0)

    @classmethod
    def _block_font(
        cls,
        block: TextBlock,
        global_font: SystemFont,
        page_id: UUID,
        issues: list[ExportIssue],
    ) -> SystemFont:
        if block.typesetting_font_family is None:
            return global_font
        try:
            return resolve_system_font(
                block.typesetting_font_family,
                block.typesetting_font_style,
            )
        except (FontResolutionError, OSError, ValueError) as error:
            issues.append(
                ExportIssue(
                    message=(
                        f"font override unavailable for block {block.id} "
                        f"({block.typesetting_font_family} / "
                        f"{block.typesetting_font_style or 'Auto'}): {error}; "
                        "using the global Thai font"
                    ),
                    page_id=page_id,
                    block_id=block.id,
                )
            )
            return global_font

    @classmethod
    def _block_style(
        cls,
        block: TextBlock,
        source_style: SourceTextStyle | None,
        background: Image.Image,
    ) -> tuple[RgbColor, RgbColor | None, int, int | None]:
        source_fill = source_style[0] if source_style is not None else None
        source_stroke = source_style[1] if source_style is not None else None
        maximum_font_size = source_style[3] if source_style is not None else None
        luminance = ImageStat.Stat(background.convert("L")).mean[0]
        fill = (
            cls._parse_rgb(block.typesetting_fill_color)
            or source_fill
            or ((0, 0, 0) if luminance >= 145 else (255, 255, 255))
        )
        stroke = cls._parse_rgb(block.typesetting_stroke_color)
        width = block.typesetting_stroke_width or 0
        if width == 0:
            stroke = None
        elif stroke is None:
            stroke = source_stroke or cls._default_stroke_color(fill)
        return fill, stroke, width or 0, maximum_font_size

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
    def _block_bounds(
        block: TextBlock,
        image_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        image_width, image_height = image_size
        bbox = clamp_bbox(
            rotated_bbox_bounds(block.bbox, block.rotation_degrees),
            image_width,
            image_height,
        )
        left = max(0, int(bbox.x))
        top = max(0, int(bbox.y))
        right = min(image_width, max(left + 1, int(bbox.x + bbox.width + 0.999)))
        bottom = min(image_height, max(top + 1, int(bbox.y + bbox.height + 0.999)))
        return left, top, right, bottom

    @staticmethod
    def _region_mask(
        block: TextBlock,
        bounds: tuple[int, int, int, int],
    ) -> Image.Image:
        left, top, right, bottom = bounds
        mask = Image.new("L", (right - left, bottom - top))
        if block.rotation_degrees == 0.0:
            region_left = max(0, int(block.bbox.x) - left)
            region_top = max(0, int(block.bbox.y) - top)
            region_right = min(mask.width, int(block.bbox.x + block.bbox.width + 0.999) - left)
            region_bottom = min(
                mask.height,
                int(block.bbox.y + block.bbox.height + 0.999) - top,
            )
            ImageDraw.Draw(mask).rectangle(
                (region_left, region_top, region_right - 1, region_bottom - 1),
                fill="white",
            )
            return mask
        points = [
            (round(x - left), round(y - top))
            for x, y in rotated_bbox_corners(block.bbox, block.rotation_degrees)
        ]
        ImageDraw.Draw(mask).polygon(points, fill="white")
        return mask

    @staticmethod
    def _manual_cleanup_bounds(
        bounds: tuple[int, int, int, int],
        image_size: tuple[int, int],
    ) -> tuple[int, int, int, int]:
        left, top, right, bottom = bounds
        image_width, image_height = image_size
        return (
            max(0, left - _MANUAL_CLEANUP_MARGIN),
            max(0, top - _MANUAL_CLEANUP_MARGIN),
            min(image_width, right + _MANUAL_CLEANUP_MARGIN),
            min(image_height, bottom + _MANUAL_CLEANUP_MARGIN),
        )

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
                        "text_alignment": block.typesetting_alignment.value,
                        "bbox_x": block.bbox.x,
                        "bbox_y": block.bbox.y,
                        "bbox_width": block.bbox.width,
                        "bbox_height": block.bbox.height,
                        "rotation_degrees": block.rotation_degrees,
                        "mirror_horizontal": block.mirror_horizontal,
                        "mirror_vertical": block.mirror_vertical,
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
        on_metric: RenderMetricCallback | None = None,
        *,
        watermark_text: str | None = None,
        watermark_logo: Image.Image | None = None,
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
        text_styles: dict[UUID, SourceTextStyle] = {}
        font_sources: dict[UUID, SystemFont] = {}
        skipped_blocks: set[UUID] = set()
        global_font = cls._global_font(font_file)
        for block in blocks:
            token.raise_if_cancelled()
            try:
                left, top, right, bottom = cls._block_bounds(block, image.size)
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
            render_tasks.append((block, left, top, right, bottom))
            style = cls._estimate_source_text_style(
                original,
                (left, top, right, bottom),
                block.source_text,
            )
            if style is not None:
                text_styles[block.id] = style
            font_sources[block.id] = cls._block_font(block, global_font, page.id, issues)

        for block, left, top, right, bottom in render_tasks:
            token.raise_if_cancelled()
            region_mask = cls._region_mask(block, (left, top, right, bottom))
            if clean_background:
                override_path = cls._find_manual_cleanup(project_directory, page, block)
                if override_path is not None:
                    cleanup_left, cleanup_top, cleanup_right, cleanup_bottom = (
                        cls._manual_cleanup_bounds((left, top, right, bottom), image.size)
                    )
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
                    cleanup_mask = cls._region_mask(
                        block,
                        (cleanup_left, cleanup_top, cleanup_right, cleanup_bottom),
                    )
                    image.paste(override, (cleanup_left, cleanup_top), cleanup_mask)
                    continue
                outer_left = max(0, left - _CLEANUP_RING_MARGIN)
                outer_top = max(0, top - _CLEANUP_RING_MARGIN)
                outer_right = min(image.width, right + _CLEANUP_RING_MARGIN)
                outer_bottom = min(image.height, bottom + _CLEANUP_RING_MARGIN)
                sample = original.crop((outer_left, outer_top, outer_right, outer_bottom))
                ring_mask = Image.new("L", sample.size, "white")
                ImageDraw.Draw(ring_mask).polygon(
                    [
                        (round(x - outer_left), round(y - outer_top))
                        for x, y in rotated_bbox_corners(
                            block.bbox,
                            block.rotation_degrees,
                        )
                    ],
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
                    cleaned = None
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
                    image.paste(background, (left, top, right, bottom), region_mask)
                else:
                    cleaned_mask = cls._region_mask(
                        block,
                        (
                            clean_left,
                            clean_top,
                            clean_left + cleaned.width,
                            clean_top + cleaned.height,
                        ),
                    )
                    image.paste(cleaned, (clean_left, clean_top), cleaned_mask)
            else:
                image.paste(background_color, (left, top, right, bottom), region_mask)

        for block, left, top, right, bottom in render_tasks:
            token.raise_if_cancelled()
            if block.id in skipped_blocks:
                continue
            width = max(1, round(block.bbox.width))
            height = max(1, round(block.bbox.height))
            style = text_styles.get(block.id)
            fill, stroke_fill, stroke_width, maximum_font_size = cls._block_style(
                block,
                style,
                image.crop((left, top, right, bottom)),
            )
            metric_callback: TextMetricCallback | None = None
            if on_metric is not None:

                def emit_metric(
                    font_size_px: int,
                    fill_rgb: RgbColor,
                    stroke_rgb: RgbColor | None,
                    stroke_width_px: int,
                ) -> None:
                    on_metric(
                        RenderMetric(
                            block_id=block.id,
                            font_size_px=font_size_px,
                            fill_rgb=fill_rgb,
                            stroke_rgb=stroke_rgb,
                            font_label=font_sources[block.id].label,
                            stroke_width_px=stroke_width_px,
                        )
                    )

                metric_callback = emit_metric
            rendered, overflow = cls._render_text_box(
                block.translated_text,
                width,
                height,
                font_sources[block.id],
                image.crop((left, top, right, bottom)),
                fill_color=fill,
                stroke_color=stroke_fill,
                stroke_width=stroke_width,
                maximum_font_size=maximum_font_size,
                font_size=block.typesetting_font_size,
                line_spacing=block.typesetting_line_spacing,
                text_alignment=block.typesetting_alignment,
                mirror_horizontal=block.mirror_horizontal,
                mirror_vertical=block.mirror_vertical,
                transparent_background=True,
                on_metric=metric_callback,
            )
            if block.rotation_degrees:
                rendered = rendered.rotate(
                    -block.rotation_degrees,
                    resample=Image.Resampling.BICUBIC,
                    expand=True,
                )
            center_x = block.bbox.x + block.bbox.width / 2
            center_y = block.bbox.y + block.bbox.height / 2
            paste_position = (
                round(center_x - rendered.width / 2),
                round(center_y - rendered.height / 2),
            )
            image.paste(rendered, paste_position, rendered)
            if overflow:
                ImageDraw.Draw(image).line(
                    [
                        *rotated_bbox_corners(block.bbox, block.rotation_degrees),
                        rotated_bbox_corners(block.bbox, block.rotation_degrees)[0],
                    ],
                    fill="red",
                    width=min(3, width, height),
                )
                warnings.append(
                    OverflowWarning(
                        message=(
                            "Thai translation does not fit at selected font size"
                            if block.typesetting_font_size is not None
                            else "Thai translation does not fit at minimum font size"
                        ),
                        page_id=page.id,
                        block_id=block.id,
                    )
                )

        for block, left, top, right, bottom in render_tasks:
            if block.id in skipped_blocks:
                image.paste(
                    original.crop((left, top, right, bottom)),
                    (left, top),
                    cls._region_mask(block, (left, top, right, bottom)),
                )

        token.raise_if_cancelled()
        image = cls._apply_watermark(
            image,
            cls._global_font(font_file),
            watermark_text,
            watermark_logo,
        )
        cls._atomic_image(destination, image)
        return warnings, issues

    @staticmethod
    def _load_export_image(
        value: str | os.PathLike[str] | None,
        label: str,
    ) -> tuple[Path | None, Image.Image | None]:
        if value is None:
            return None, None
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{label} image not found: {path}")
        try:
            with Image.open(path) as source:
                source.load()
                mode = "RGBA" if "A" in source.getbands() else "RGB"
                return path, source.convert(mode)
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"invalid {label} image {path}: {error}") from error

    @classmethod
    def _load_watermark(
        cls,
        text: str | None,
        logo_path: str | os.PathLike[str] | None,
    ) -> tuple[str | None, Path | None, Image.Image | None]:
        normalized = text.strip() if text is not None else None
        if text is not None and not normalized:
            raise ValueError("watermark text cannot be empty")
        if normalized is not None and logo_path is not None:
            raise ValueError("choose watermark text or logo, not both")
        path, logo = cls._load_export_image(logo_path, "watermark logo")
        return normalized, path, logo

    @staticmethod
    def _apply_watermark(
        image: Image.Image,
        font_source: SystemFont,
        text: str | None,
        logo: Image.Image | None,
    ) -> Image.Image:
        if text is None and logo is None:
            return image
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        margin = max(8, min(image.size) // 50)
        if logo is not None:
            mark = logo.copy()
            maximum = (
                max(1, round(image.width * _WATERMARK_MAX_LOGO_RATIO)),
                max(1, round(image.height * _WATERMARK_MAX_LOGO_RATIO)),
            )
            mark.thumbnail(maximum, Image.Resampling.LANCZOS)
            overlay.alpha_composite(
                mark.convert("RGBA"),
                (image.width - margin - mark.width, image.height - margin - mark.height),
            )
        else:
            assert text is not None
            font_size = max(12, min(72, round(min(image.size) * 0.04)))
            font = load_pillow_font(font_source, font_size)
            draw = ImageDraw.Draw(overlay)
            stroke_width = max(1, font_size // 14)
            bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
            width = bounds[2] - bounds[0]
            maximum_width = max(1, round(image.width * _WATERMARK_MAX_TEXT_RATIO))
            if width > maximum_width:
                font_size = max(8, round(font_size * maximum_width / width))
                font = load_pillow_font(font_source, font_size)
                stroke_width = max(1, font_size // 14)
                bounds = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
            width = bounds[2] - bounds[0]
            height = bounds[3] - bounds[1]
            draw.text(
                (
                    image.width - margin - width - bounds[0],
                    image.height - margin - height - bounds[1],
                ),
                text,
                font=font,
                fill=(255, 255, 255, 178),
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 204),
            )
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

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
    def _estimate_source_text_style(
        image: Image.Image,
        bounds: tuple[int, int, int, int],
        source_text: str,
    ) -> SourceTextStyle | None:
        if not source_text.strip():
            return None
        crop = image.crop(bounds).convert("RGB")
        if crop.width < 3 or crop.height < 3:
            return None

        pixels = crop.load()
        edge_pixels = [pixels[x, 0] for x in range(crop.width)]
        edge_pixels.extend(pixels[x, crop.height - 1] for x in range(crop.width))
        edge_pixels.extend(pixels[0, y] for y in range(1, crop.height - 1))
        edge_pixels.extend(pixels[crop.width - 1, y] for y in range(1, crop.height - 1))
        edge = Image.new("RGB", (len(edge_pixels), 1))
        edge.putdata(edge_pixels)
        background = tuple(int(value) for value in ImageStat.Stat(edge).median)

        def distance(first: RgbColor, second: RgbColor) -> int:
            return max(abs(first[index] - second[index]) for index in range(3))

        quantized = crop.quantize(colors=12, method=Image.Quantize.MEDIANCUT).convert("RGB")
        minimum_count = max(3, crop.width * crop.height // 1500)
        candidates: list[tuple[int, RgbColor, int]] = []
        for count, color in quantized.getcolors(maxcolors=12) or []:
            rgb = tuple(int(value) for value in color)
            color_distance = distance(rgb, background)
            if count >= minimum_count and color_distance >= 32:
                candidates.append((count, rgb, color_distance))
        if not candidates:
            return None

        strongest_distance = max(item[2] for item in candidates)
        strong_candidates = [item for item in candidates if item[2] * 4 >= strongest_distance * 3]
        fill_count, fill, fill_distance = max(strong_candidates, key=lambda item: item[0])
        stroke_candidates = [
            item
            for item in candidates
            if item[1] != fill
            and item[0] * 5 >= fill_count
            and distance(item[1], fill) >= 45
            and item[2] >= 35
        ]
        stroke = max(stroke_candidates, key=lambda item: item[0])[1] if stroke_candidates else None

        threshold = max(24, fill_distance // 5)
        foreground = Image.new("L", crop.size)
        foreground.putdata(
            [
                255
                if distance(tuple(int(value) for value in pixels[x, y]), background) >= threshold
                else 0
                for y in range(crop.height)
                for x in range(crop.width)
            ]
        )
        foreground_bounds = foreground.getbbox()
        if foreground_bounds is None:
            return None
        line_count = max(1, len(source_text.splitlines()))
        glyph_height = foreground_bounds[3] - foreground_bounds[1]
        maximum_font_size = max(
            _MIN_FONT_SIZE,
            min(_MAX_FONT_SIZE, round(glyph_height / line_count * 1.1)),
        )
        stroke_width = max(1, min(6, maximum_font_size // 18)) if stroke is not None else 0
        return fill, stroke, stroke_width, maximum_font_size

    @classmethod
    def _render_text_box(
        cls,
        text: str,
        width: int,
        height: int,
        font_file: SystemFont | Path,
        background: RgbColor | Image.Image,
        *,
        fill_color: RgbColor | None = None,
        stroke_color: RgbColor | None = None,
        stroke_width: int = 0,
        maximum_font_size: int | None = None,
        font_size: int | None = None,
        line_spacing: int | None = None,
        text_alignment: TextAlignment = TextAlignment.CENTER,
        mirror_horizontal: bool = False,
        mirror_vertical: bool = False,
        transparent_background: bool = False,
        on_metric: TextMetricCallback | None = None,
    ) -> tuple[Image.Image, bool]:
        canvas = (
            Image.new("RGBA", (width, height))
            if transparent_background
            else (
                background.copy().convert("RGB")
                if isinstance(background, Image.Image)
                else Image.new("RGB", (width, height), background)
            )
        )
        draw = ImageDraw.Draw(canvas)
        luminance = (
            ImageStat.Stat(background.convert("L")).mean[0]
            if isinstance(background, Image.Image)
            else Image.new("RGB", (1, 1), background).convert("L").getpixel((0, 0))
        )
        fill = fill_color or ((0, 0, 0) if luminance >= 145 else (255, 255, 255))
        stroke_fill = stroke_color or ((255, 255, 255) if fill == (0, 0, 0) else (0, 0, 0))
        padding_divisor = 20 if maximum_font_size is not None else 10
        padding = max(1, min(width, height) // padding_divisor)
        available_width = max(1, width - 2 * padding)
        available_height = max(1, height - 2 * padding)
        selected: (
            tuple[ImageFont.FreeTypeFont, list[str], int, int, tuple[int, int, int, int]] | None
        ) = None
        maximum_size = font_size or min(
            _MAX_FONT_SIZE,
            max(
                _MIN_FONT_SIZE,
                min(available_height, maximum_font_size or available_height),
            ),
        )
        sizes = (
            (maximum_size,)
            if font_size is not None
            else range(maximum_size, _MIN_FONT_SIZE - 1, -1)
        )
        for size in sizes:
            font = load_pillow_font(font_file, size)
            lines = cls._wrap_text(draw, text, font, available_width)
            spacing = line_spacing if line_spacing is not None else max(1, size // 3)
            rendered_stroke_width = min(stroke_width, max(1, size // 18)) if stroke_width else 0
            bounds = draw.multiline_textbbox(
                (0, 0),
                "\n".join(lines),
                font=font,
                spacing=spacing,
                stroke_width=rendered_stroke_width,
                align=text_alignment.value,
            )
            selected = font, lines, spacing, rendered_stroke_width, bounds
            text_width = bounds[2] - bounds[0]
            text_height = bounds[3] - bounds[1]
            if text_width <= available_width and text_height <= available_height:
                break

        assert selected is not None
        font, lines, spacing, stroke_width, bounds = selected
        if on_metric is not None:
            on_metric(font.size, fill, stroke_fill if stroke_width else None, stroke_width)
        text_width = bounds[2] - bounds[0]
        text_height = bounds[3] - bounds[1]
        overflow = text_width > available_width or text_height > available_height
        if text_alignment is TextAlignment.LEFT:
            x = padding - bounds[0]
        elif text_alignment is TextAlignment.RIGHT:
            x = width - padding - text_width - bounds[0]
        else:
            x = (width - text_width) / 2 - bounds[0]
        position = (x, (height - text_height) / 2 - bounds[1])
        text_layer = Image.new("RGBA", canvas.size)
        ImageDraw.Draw(text_layer).multiline_text(
            position,
            "\n".join(lines),
            fill=(*fill, 255),
            font=font,
            spacing=spacing,
            stroke_width=stroke_width,
            stroke_fill=(*stroke_fill, 255),
            align=text_alignment.value,
        )
        if mirror_horizontal:
            text_layer = text_layer.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if mirror_vertical:
            text_layer = text_layer.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if transparent_background:
            canvas = text_layer
        else:
            composed = canvas.convert("RGBA")
            composed.alpha_composite(text_layer)
            canvas = composed.convert("RGB")
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
                thai_word_breaks = cls._thai_word_breaks("".join(clusters), clusters)
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
                break_candidates = [
                    index for index in range(1, fit + 1) if clusters[index - 1].isspace()
                ]
                break_candidates.extend(index for index in thai_word_breaks if index <= fit)
                consume = max(break_candidates, default=fit)
                lines.append("".join(clusters[:consume]).rstrip())
                clusters = clusters[consume:]
                while clusters and clusters[0].isspace():
                    clusters.pop(0)
        return lines

    @staticmethod
    def _thai_word_breaks(text: str, clusters: list[str]) -> set[int]:
        if not any("\u0e00" <= character <= "\u0e7f" for character in text):
            return set()
        utf16_offsets = [0]
        for cluster in clusters:
            utf16_offsets.append(utf16_offsets[-1] + len(cluster.encode("utf-16-le")) // 2)
        cluster_indexes = {offset: index for index, offset in enumerate(utf16_offsets)}
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Word, text)
        boundaries: set[int] = set()
        while (boundary := finder.toNextBoundary()) != -1:
            cluster_index = cluster_indexes.get(boundary)
            if cluster_index is None or not 0 < cluster_index < len(clusters):
                continue
            previous = clusters[cluster_index - 1]
            current = clusters[cluster_index]
            if any("\u0e00" <= character <= "\u0e7f" for character in previous) or any(
                "\u0e00" <= character <= "\u0e7f" for character in current
            ):
                boundaries.add(cluster_index)
        return boundaries

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
