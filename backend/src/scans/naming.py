"""Filename conventions used across the OneDrive document tree.

Dated documents follow ``YYYY-MM-DD[_HH-MM][_slug].pdf`` (for example
``2026-09-27_19-55_trader_joes.pdf``). Undated reference documents such as
manuals use a bare snake_case slug (``dewalt_dcs334.pdf``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Iterable, Optional

_DATED_NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}(?:-\d{2})?)"
    r"(?:[_-](?P<time>\d{2}[-.]\d{2})(?=_|$))?"
    r"(?:[_-](?P<slug>.+))?$"
)
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")


@dataclass(frozen=True)
class ParsedName:
    date: Optional[str]
    time: Optional[str]
    slug: Optional[str]


def slugify(text: str, max_words: Optional[int] = None) -> str:
    """Convert free text into the snake_case slug style used in filenames."""
    text = text.replace("’", "").replace("'", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    if max_words:
        text = "_".join(text.split("_")[:max_words])
    return text


def parse_filename(name: str) -> ParsedName:
    """Split an existing filename into date, time, and slug parts."""
    stem = _COPY_SUFFIX_RE.sub("", Path(name).stem)
    match = _DATED_NAME_RE.match(stem)
    if not match:
        return ParsedName(date=None, time=None, slug=slugify(stem) or None)
    raw_time = match.group("time")
    return ParsedName(
        date=match.group("date"),
        time=raw_time.replace(".", "-") if raw_time else None,
        slug=slugify(match.group("slug")) if match.group("slug") else None,
    )


_NUMBERED_COPY_RE = re.compile(r"[_-][2-9]$")


def merchant_of(parsed: ParsedName) -> Optional[str]:
    """The merchant in a dated name, without the ``_2``/``_3`` that tells same-name receipts apart."""
    if not parsed.date or not parsed.slug:
        return None
    base = _NUMBERED_COPY_RE.sub("", parsed.slug)
    return base or parsed.slug


def format_date(value: Optional[date]) -> Optional[str]:
    return value.strftime("%Y-%m-%d") if value else None


def format_time(value: Optional[time]) -> Optional[str]:
    return value.strftime("%H-%M") if value else None


def build_filename(
    date_part: Optional[str],
    time_part: Optional[str],
    slug: Optional[str],
    extension: str = ".pdf",
) -> str:
    """Build a filename, omitting any part that is unknown."""
    parts = [p for p in (date_part, time_part if date_part else None, slug) if p]
    if not parts:
        parts = ["unnamed_scan"]
    return "_".join(parts) + extension


def unique_path(directory: Path, filename: str, taken: Iterable[Path] = ()) -> Path:
    """Return a path in ``directory`` that does not collide with existing files.

    Collisions get ``_2``, ``_3`` ... appended to the stem, never overwriting.
    """
    taken_set = {Path(p).resolve() for p in taken}
    candidate = directory / filename
    stem, suffix = candidate.stem, candidate.suffix
    counter = 2
    while candidate.exists() or candidate.resolve() in taken_set:
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate
