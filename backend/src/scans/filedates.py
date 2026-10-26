"""Make file timestamps match the document date in the filename.

``2026-09-26_14-05_trader_joes.pdf`` gets created/modified times of
2026-09-26 14:05 local time. Names without a time use noon so the day never
shifts across time zones; ``YYYY-MM`` names use the 1st of the month.
On Windows the creation time is set as well as the modified time.

Only timestamp metadata changes. Online-only OneDrive placeholders stay
online-only; this was checked against a dehydrated file on the real OneDrive.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from .naming import parse_filename

# Windows file attributes for OneDrive placeholders that are not on disk.
_ONLINE_ONLY_ATTRS = 0x00400000 | 0x00001000  # RECALL_ON_DATA_ACCESS | OFFLINE
_TOLERANCE = timedelta(minutes=1)


def document_datetime(name: str, today: Optional[datetime] = None) -> Optional[datetime]:
    """Datetime encoded in a convention filename, or None if undated/implausible."""
    parsed = parse_filename(name)
    if not parsed.date:
        return None
    parts = [int(p) for p in parsed.date.split("-")]
    year, month, day = (parts + [1, 1])[:3]
    hour, minute = (12, 0)
    if parsed.time:
        hour, minute = (int(p) for p in parsed.time.split("-"))
    try:
        value = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    today = today or datetime.now()
    if not datetime(1900, 1, 1) <= value <= today + timedelta(days=1):
        return None
    return value


def is_online_only(path: Path) -> bool:
    attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
    return bool(attrs & _ONLINE_ONLY_ATTRS)


def _set_windows_creation_time(path: Path, when: datetime) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.SetFileTime.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 3
    file_write_attributes, share_all, open_existing = 0x100, 0x7, 3
    handle = kernel32.CreateFileW(str(path), file_write_attributes, share_all, None, open_existing, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ticks = int((when.timestamp() + 11644473600) * 10_000_000)
        created = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        if not kernel32.SetFileTime(handle, ctypes.byref(created), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def set_file_times(path: Path, when: datetime) -> None:
    timestamp = when.timestamp()
    os.utime(path, (timestamp, timestamp))
    if sys.platform == "win32":
        _set_windows_creation_time(path, when)


def times_match(path: Path, when: datetime) -> bool:
    stat = os.stat(path)
    modified = datetime.fromtimestamp(stat.st_mtime)
    ok = abs(modified - when) <= _TOLERANCE
    if sys.platform == "win32":
        created = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime))
        ok = ok and abs(created - when) <= _TOLERANCE
    return ok


@dataclass
class RestampReport:
    changed: list[tuple[Path, datetime]] = field(default_factory=list)
    already_ok: int = 0
    undated: int = 0
    skipped_online_only: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def restamp(
    roots: Iterable[Path],
    dry_run: bool = True,
    include_online_only: bool = False,
) -> RestampReport:
    """Set times on every convention-dated file under ``roots``. Content is never touched."""
    report = RestampReport()
    seen: set[Path] = set()
    for root in roots:
        if not Path(root).exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = Path(dirpath) / name
                if path in seen or name.lower() == "desktop.ini":
                    continue
                seen.add(path)
                when = document_datetime(name)
                if when is None:
                    report.undated += 1
                    continue
                try:
                    if times_match(path, when):
                        report.already_ok += 1
                        continue
                    if not include_online_only and is_online_only(path):
                        report.skipped_online_only.append(path)
                        continue
                    if not dry_run:
                        set_file_times(path, when)
                    report.changed.append((path, when))
                except OSError as exc:
                    report.errors.append(f"{path}: {exc}")
    return report
