"""Small Linux system-font bridge for Qt discovery and Pillow rendering."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont
from PySide6.QtGui import QFontDatabase

_FC_MATCH_FORMAT = "%{family[0]}\t%{style[0]}\t%{file}\t%{index}\n"
_VARIATION_INDEX = 1 << 16


class FontResolutionError(ValueError):
    """The requested system font could not be resolved without a fallback."""


@dataclass(frozen=True, slots=True)
class SystemFont:
    family: str
    style: str
    path: Path
    face_index: int = 0
    variation_name: str | None = None
    display_label: str | None = None

    @property
    def label(self) -> str:
        return self.display_label or f"{self.family} / {self.style}"


def thai_font_families() -> tuple[str, ...]:
    """Return installed families that Qt advertises for Thai text."""
    return tuple(
        sorted(
            set(QFontDatabase.families(QFontDatabase.WritingSystem.Thai)),
            key=str.casefold,
        )
    )


def font_styles(family: str) -> tuple[str, ...]:
    """Return styles exposed by Qt for one family."""
    return tuple(sorted(set(QFontDatabase.styles(family)), key=str.casefold))


def _variation_name(index: int, style: str) -> tuple[int, str | None]:
    # fontconfig encodes named variable-font instances in the high 16 bits.
    if index >= _VARIATION_INDEX:
        return index & 0xFFFF, style
    return index, None


def resolve_system_font(family: str, style: str | None = None) -> SystemFont:
    """Resolve an exact family/style to a Pillow-loadable font face."""
    if not family:
        raise FontResolutionError("font family is required")
    query = f"{family}:style={style}" if style else family
    try:
        result = subprocess.run(
            ["fc-match", "-f", _FC_MATCH_FORMAT, query],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise FontResolutionError(f"fontconfig could not resolve {family} / {style}") from error
    fields = result.stdout.strip().split("\t")
    if len(fields) != 4:
        raise FontResolutionError(f"fontconfig returned malformed data for {family} / {style}")
    resolved_family, resolved_style, raw_path, raw_index = fields
    if resolved_family != family or (style is not None and resolved_style != style):
        raise FontResolutionError(
            f"fontconfig fallback {resolved_family} / {resolved_style} for {family} / "
            f"{style or 'default'}"
        )
    try:
        index = int(raw_index)
    except ValueError as error:
        raise FontResolutionError(
            f"fontconfig returned invalid face index for {family} / {style}"
        ) from error
    face_index, variation_name = _variation_name(index, resolved_style)
    resolved = SystemFont(
        family=family,
        style=resolved_style,
        path=Path(raw_path),
        face_index=face_index,
        variation_name=variation_name,
    )
    try:
        load_pillow_font(resolved, 12)
    except (OSError, ValueError) as error:
        raise FontResolutionError(f"Pillow could not load {family} / {resolved_style}") from error
    return resolved


def load_pillow_font(font: SystemFont | str | Path, size: int) -> ImageFont.FreeTypeFont:
    """Load a static face or a named variable-font instance with Pillow."""
    if isinstance(font, SystemFont):
        loaded = ImageFont.truetype(font.path, size, index=font.face_index)
        if font.variation_name is not None:
            setter = getattr(loaded, "set_variation_by_name", None)
            if setter is None:
                raise OSError(f"variable font style is unsupported: {font.label}")
            setter(font.variation_name)
        return loaded
    return ImageFont.truetype(Path(font), size)
