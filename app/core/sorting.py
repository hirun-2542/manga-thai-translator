"""Natural filename sorting."""

import os
import re
import unicodedata
from collections.abc import Iterable

_NUMBER = re.compile(r"(\d+)")
type NaturalPart = tuple[int, str] | tuple[int, int, int]


def natural_sort_key(
    value: str | os.PathLike[str],
) -> tuple[tuple[NaturalPart, ...], str, str]:
    """Return a case-insensitive, Unicode-normalized natural-sort key."""
    original = os.fspath(value)
    normalized = unicodedata.normalize("NFKC", original)
    parts: list[NaturalPart] = []
    for part in _NUMBER.split(normalized):
        if part.isdecimal():
            parts.append((1, int(part), len(part)))
        else:
            parts.append((0, part.casefold()))
    return tuple(parts), normalized, original


def natural_sorted[PathValue: str | os.PathLike[str]](
    values: Iterable[PathValue],
) -> list[PathValue]:
    return sorted(values, key=natural_sort_key)
