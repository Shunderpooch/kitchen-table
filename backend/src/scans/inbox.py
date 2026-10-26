"""Scan inbox: scans saved to one OneDrive folder become review batches.

ScanSmart on any computer saves into ``OneDrive/Scans/Inbox`` (a subfolder
named after a person, e.g. ``Inbox/Sam``, marks whose documents they are).
OneDrive brings the files to this PC, where ``inbox_tick`` (run every few
minutes by a scheduled task):

1. waits until nothing in the inbox has changed for a while (a scan or a
   sync still in progress),
2. moves the scans to ``OneDrive/Scans/Originals/<batch>/`` (kept: filing
   copies pages, it never deletes a scan),
3. cuts them into documents and proposes names and folders (``plan_batch``),
   writing the manifest to ``data/scans/<batch>/``,
4. renders page previews for the review dashboard.

Nothing is filed until the batch is reviewed and confirmed in the app.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .conventions import SELF_KEY, OneDriveLayout
from .cutter import IMAGE_EXTENSIONS, PageRef, load_page_image
from .pipeline import plan_batch, read_manifest, write_manifest

SETTLE_SECONDS = 90
LOCK_STALE_SECONDS = 3 * 3600
THUMB_SIZE = (220, 300)
PREVIEW_SIZE = (1400, 2000)
SCAN_SUFFIXES = {".pdf", *IMAGE_EXTENSIONS}


def scans_root(layout: OneDriveLayout) -> Path:
    return layout.onedrive / "Scans"


@dataclass
class TickResult:
    batches: list[str] = field(default_factory=list)
    waiting: Optional[str] = None  # why nothing was picked up yet
    errors: list[str] = field(default_factory=list)


def _scan_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SCAN_SUFFIXES and not p.name.startswith(("~", ".")))


def pending_scans(inbox: Path) -> dict[Optional[str], list[Path]]:
    """Scans waiting in the inbox, by person subfolder (None: the owner's)."""
    if not inbox.is_dir():
        return {}
    found: dict[Optional[str], list[Path]] = {}
    if files := _scan_files(inbox):
        found[None] = files
    for sub in sorted(p for p in inbox.iterdir() if p.is_dir()):
        if files := _scan_files(sub):
            found[sub.name] = files
    return found


class _Lock:
    """One tick at a time (the scheduled task can overlap a long OCR run)."""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}")
        self.held = True
        return True

    def __exit__(self, *exc) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)


def new_batch_id(scans_dir: Path) -> str:
    base = f"{datetime.now():%Y%m%d_%H%M%S}"
    candidate, n = base, 1
    while (scans_dir / candidate).exists():
        candidate, n = f"{base}_{n}", n + 1
    return candidate


def render_pages(manifest_path: Path) -> list[dict]:
    """Thumbnails and previews of every page, in scan order, for the review dashboard."""
    rows = sorted(read_manifest(manifest_path), key=lambda r: r["item_id"])
    out = manifest_path.parent / "pages"
    out.mkdir(exist_ok=True)
    pages = []
    for row in rows:
        for ref_text in (p.strip() for p in row["pages"].split("|") if p.strip()):
            n = len(pages) + 1
            entry = {"n": n, "item_id": row["item_id"], "ref": ref_text, "width": 0, "height": 0}
            try:
                image = load_page_image(PageRef.decode(ref_text))
            except Exception:  # an unreadable page still gets a placeholder in the strip
                image = None
            if image is not None:
                image = image.convert("RGB")
                entry["width"], entry["height"] = image.size
                preview = image.copy()
                preview.thumbnail(PREVIEW_SIZE)
                preview.save(out / f"{n:04d}.jpg", quality=82)
                image.thumbnail(THUMB_SIZE)
                image.save(out / f"{n:04d}_t.jpg", quality=75)
            pages.append(entry)
    (manifest_path.parent / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    return pages


def plan_inbox_batch(layout: OneDriveLayout, source: Path, batch_dir: Path, person: str,
                     ocr: Optional[Callable]) -> Path:
    page_texts: dict[str, str] = {}
    rows = plan_batch(source, layout, person=person, ocr=ocr, page_texts=page_texts)
    manifest = batch_dir / "manifest.csv"
    write_manifest(rows, manifest, page_texts=page_texts)
    render_pages(manifest)
    return manifest


def inbox_tick(layout: OneDriveLayout, scans_dir: Path, ocr: Optional[Callable],
               inbox: Optional[Path] = None, settle_seconds: int = SETTLE_SECONDS,
               now: Optional[float] = None) -> TickResult:
    result = TickResult()
    root = scans_root(layout)
    inbox = inbox or root / "Inbox"
    if (scans_dir / "PAUSED").exists():
        result.waiting = "paused (delete data/scans/PAUSED to resume)"
        return result
    with _Lock(scans_dir / "inbox.lock") as locked:
        if not locked:
            result.waiting = "another inbox run is in progress"
            return result
        found = pending_scans(inbox)
        if not found:
            return result
        newest = max(p.stat().st_mtime for files in found.values() for p in files)
        if (now or time.time()) - newest < settle_seconds:
            result.waiting = "a scan is still arriving"
            return result
        for folder, files in found.items():
            try:
                person = layout.resolve_person(folder) if folder else SELF_KEY
            except ValueError as exc:
                result.errors.append(f"Inbox/{folder}: {exc}")
                continue
            batch_id = new_batch_id(scans_dir)
            batch_dir = scans_dir / batch_id
            originals = root / "Originals" / batch_id
            originals.mkdir(parents=True)
            moved: list[tuple[Path, Path]] = []
            try:
                for path in files:
                    # A .txt beside a scan is its text (as the cutter reads it).
                    for part in (path, path.with_suffix(".txt")):
                        if part == path or part.exists():
                            target = originals / part.name
                            shutil.move(str(part), target)
                            moved.append((part, target))
            except OSError as exc:  # still being written or synced: try again next tick
                for back, there in reversed(moved):
                    shutil.move(str(there), back)
                originals.rmdir()
                result.errors.append(f"could not move {path.name} yet: {exc}")
                continue
            batch_dir.mkdir(parents=True)
            (batch_dir / "batch.json").write_text(json.dumps({
                "origin": "inbox", "originals": str(originals), "person": person,
                "files": [t.name for _, t in moved if t.suffix.lower() in SCAN_SUFFIXES], "created": datetime.now().isoformat(timespec="seconds"),
            }, indent=2), encoding="utf-8")
            log = batch_dir / "plan.log"
            try:
                manifest = plan_inbox_batch(layout, originals, batch_dir, person, ocr)
                rows = read_manifest(manifest)
                documents = [r for r in rows if not r["merge_into"]]
                review = sum(r["action"] == "review" for r in documents)
                log.write_text(f"{len(documents)} documents from {len(rows)} scanned items; "
                               f"{review} need a closer look\n", encoding="utf-8")
                (batch_dir / "plan.done").write_text("0", encoding="utf-8")
                result.batches.append(batch_id)
            except Exception as exc:
                log.write_text(traceback.format_exc(), encoding="utf-8")
                (batch_dir / "plan.done").write_text("1", encoding="utf-8")
                result.errors.append(f"{batch_id}: {exc}")
    return result


def lower_priority() -> None:
    """OCR runs in the background on a PC that is also used for games."""
    if os.name == "nt":
        import ctypes

        below_normal = 0x00004000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), below_normal)
    else:
        os.nice(10)
