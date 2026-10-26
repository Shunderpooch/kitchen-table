"""Plan -> review -> apply workflow for scan batches.

``plan`` never touches OneDrive; it writes an editable CSV manifest.
``apply`` re-derives names and folders from the (possibly edited) manifest,
validates every row before writing anything, copies new PDFs into OneDrive
without overwriting, and records an apply log that ``undo`` can reverse.
The original scans are never moved or deleted.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from .classify import decide, describe, destination_folder, filename_for
from .conventions import DOC_KIND_FOLDERS, SELF_KEY, OneDriveLayout
from .cutter import PageRef, discover_items, write_pdf
from .extract import extract_fields, parse_manifest_date, parse_manifest_time
from .filedates import document_datetime, set_file_times
from .grouping import group_units, make_unit
from .naming import format_date, format_time, slugify, unique_path

MANIFEST_COLUMNS = [
    "item_id", "action", "kind", "person", "category", "car", "date", "time",
    "merchant", "total", "filename", "filename_override", "destination",
    "merge_into", "reasons", "pages", "text_preview",
]
ACTIONS = {"file", "review", "skip"}
KINDS = {"receipt", "unknown", *DOC_KIND_FOLDERS}


def plan_batch(
    input_dir: Path,
    layout: OneDriveLayout,
    person: str = SELF_KEY,
    kind: Optional[str] = None,
    duplex: bool = False,
    keep_pdfs_whole: bool = False,
    ocr: Optional[Callable] = None,
    today: Optional[date] = None,
    separator_sheets: bool = False,
    group_pages: bool = True,
    page_texts: Optional[dict[str, str]] = None,
) -> list[dict]:
    """One row per scanned page; pages that continue a document get ``merge_into``.

    The first row of a document is named from the text of all its pages.
    Continuation rows keep their own suggested name so clearing ``merge_into``
    splits them back out.
    """
    person_key = layout.resolve_person(person)
    if kind and kind not in KINDS:
        raise ValueError(f"Unknown kind '{kind}'. Choose from: {', '.join(sorted(KINDS))}")
    items = discover_items(input_dir, duplex=duplex, keep_pdfs_whole=keep_pdfs_whole, ocr=ocr,
                           separator_sheets=separator_sheets)
    known = layout.known_merchants()
    names = layout.name_slugs()
    if page_texts is not None:
        # Kept beside the manifest so apply can index text without OCR again.
        for item in items:
            page_texts.update((ref.encode(), t) for ref, t in zip(item.pages, item.page_texts))
    units = [make_unit(item, extract_fields(item.text, known, today=today, skip_names=names, phone_book=layout.phone_book), layout)
             for item in items]
    taken: list[Path] = []
    rows = []

    def row_for(item_id: str, text: str, page_count: int, extracted, notes: list[str], reserve: bool) -> dict:
        decision = decide(layout, extracted, text, page_count, person_key, kind)
        doc_date, doc_time = describe(extracted)
        folder = destination_folder(layout, decision.kind, decision.person, decision.category, decision.car, doc_date)
        wanted = filename_for(decision.kind, doc_date, doc_time, decision.slug)
        target = unique_path(folder, wanted, taken)
        reasons = extracted.reasons + decision.reasons + notes
        if reserve:
            taken.append(target)
            if target.name != wanted:
                # Same merchant at the same minute is almost always the same receipt.
                where = "in OneDrive" if (folder / wanted).exists() else "earlier in this batch"
                reasons.append(f"possible duplicate: {wanted} already exists {where} (skip, or file as {target.name})")
                decision.needs_review = True
        return {
            "item_id": item_id,
            "action": "review" if decision.needs_review else "file",
            "kind": decision.kind,
            "person": decision.person,
            "category": decision.category or "",
            "car": decision.car or "",
            "date": doc_date or "",
            "time": doc_time or "",
            "merchant": decision.slug or "",
            "total": extracted.total or "",
            "filename": target.name,
            "filename_override": "",
            "destination": str(target.parent.relative_to(layout.onedrive)),
            "merge_into": "",
            "reasons": "; ".join(reasons),
            "pages": "",
            "text_preview": " / ".join(line.strip() for line in text.splitlines() if line.strip())[:160],
        }

    for group in group_units(units, enabled=group_pages):
        head = group.head.item
        text = "\n".join(u.item.text for u in group.units)
        page_count = sum(len(u.item.pages) for u in group.units)
        extracted = (group.head.extracted if len(group.units) == 1
                     else extract_fields(text, known, today=today, skip_names=names, phone_book=layout.phone_book))
        notes = [n for u in group.units for n in u.item.notes] + group.reasons
        row = row_for(head.item_id, text, page_count, extracted, notes, reserve=True)
        row["pages"] = " | ".join(ref.encode() for ref in head.pages)
        if group.weak:
            row["action"] = "review"
        rows.append(row)
        for unit in group.units[1:]:
            item = unit.item
            alone = row_for(item.item_id, item.text, len(item.pages), unit.extracted, item.notes, reserve=False)
            alone.update({
                "merge_into": head.item_id,
                "pages": " | ".join(ref.encode() for ref in item.pages),
                "reasons": f"part of {head.item_id} (clear merge_into to file separately); " + alone["reasons"],
            })
            rows.append(alone)
    return rows


def describe_documents(manifest_path: Path, layout: OneDriveLayout, groups: list[list[str]],
                       today: Optional[date] = None) -> dict[str, dict]:
    """Name, date, and folder for documents re-cut in review: each group is the
    item ids of one document, first item first. Read from the text of exactly
    those pages (kept beside the manifest), as ``plan_batch`` would have."""
    rows = {row["item_id"]: row for row in read_manifest(manifest_path)}
    sidecar = texts_path(manifest_path)
    page_texts = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    known, names = layout.known_merchants(), layout.name_slugs()
    described = {}
    for group in groups:
        items = [rows[i] for i in group if i in rows]
        if not items:
            continue
        refs = [p.strip() for row in items for p in row["pages"].split("|") if p.strip()]
        text = "\n".join(page_texts.get(ref, "") for ref in refs)
        extracted = extract_fields(text, known, today=today, skip_names=names, phone_book=layout.phone_book)
        head = items[0]
        decision = decide(layout, extracted, text, len(refs), layout.resolve_person(head["person"] or SELF_KEY))
        doc_date, doc_time = describe(extracted)
        described[head["item_id"]] = {
            "kind": decision.kind,
            "category": decision.category or "",
            "car": decision.car or "",
            "date": doc_date or "",
            "time": doc_time or "",
            "merchant": decision.slug or "",
            "total": extracted.total or "",
            "reasons": "; ".join(extracted.reasons + decision.reasons),
            "text_preview": " / ".join(line.strip() for line in text.splitlines() if line.strip())[:160],
        }
    return described


def texts_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.stem + ".texts.json")


def write_manifest(rows: list[dict], path: Path, page_texts: Optional[dict[str, str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so the manifest opens cleanly in Excel.
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    if page_texts is not None:
        texts_path(path).write_text(json.dumps(page_texts), encoding="utf-8")


def read_manifest(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


@dataclass
class PlannedFile:
    item_id: str
    pages: list[PageRef]
    target: Path


@dataclass
class ApplyResult:
    filed: list[PlannedFile] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    log_path: Optional[Path] = None
    index_errors: list[str] = field(default_factory=list)


def _row_target(row: dict, layout: OneDriveLayout, taken: list[Path]) -> Path:
    kind = row["kind"] or "receipt"
    if kind not in KINDS:
        raise ValueError(f"unknown kind '{kind}'")
    person = layout.resolve_person(row["person"])
    doc_date = format_date(parse_manifest_date(row["date"]))
    doc_time = format_time(parse_manifest_time(row["time"]))
    category = slugify(row["category"]) or None
    if kind in ("receipt", "unknown") and category and category not in layout.receipt_categories:
        raise ValueError(
            f"category '{category}' is not an existing receipts folder "
            f"({', '.join(layout.receipt_categories)})"
        )
    override = row.get("filename_override", "")
    if override:
        filename = override if override.lower().endswith(".pdf") else override + ".pdf"
        if Path(filename).name != filename:
            raise ValueError("filename_override must be a bare filename")
    else:
        filename = filename_for(kind, doc_date, doc_time, slugify(row["merchant"]) or None)
    folder = destination_folder(layout, kind, person, category, row.get("car") or None, doc_date)
    root = layout.onedrive.resolve()
    if not folder.resolve().is_relative_to(root):
        raise ValueError(f"destination {folder} is outside OneDrive")
    return unique_path(folder, filename, taken)


def resolve_plan(rows: list[dict], layout: OneDriveLayout) -> ApplyResult:
    """Validate every row and compute targets without writing anything."""
    result = ApplyResult()
    by_id = {row["item_id"]: row for row in rows}
    extra_pages: dict[str, list[PageRef]] = {}
    errors = []
    for row in rows:
        if row["action"] not in ACTIONS:
            errors.append(f"{row['item_id']}: action must be one of {sorted(ACTIONS)}")
        target_id = row.get("merge_into", "")
        if target_id:
            if target_id not in by_id or target_id == row["item_id"] or by_id[target_id].get("merge_into"):
                errors.append(f"{row['item_id']}: merge_into '{target_id}' is not a filed item")
                continue
            extra_pages.setdefault(target_id, []).extend(PageRef.decode(p) for p in row["pages"].split("|"))
            result.merged.append(row["item_id"])

    taken: list[Path] = []
    for row in rows:
        if row.get("merge_into") or row["action"] == "skip":
            if not row.get("merge_into"):
                result.skipped.append(row["item_id"])
            continue
        if row["action"] == "review":
            result.review.append(row["item_id"])
            continue
        try:
            pages = [PageRef.decode(p) for p in row["pages"].split("|")] + extra_pages.get(row["item_id"], [])
            missing = [p.path for p in pages if not p.path.exists()]
            if missing:
                raise ValueError(f"scan file missing: {missing[0]}")
            target = _row_target(row, layout, taken)
        except ValueError as exc:
            errors.append(f"{row['item_id']}: {exc}")
            continue
        taken.append(target)
        result.filed.append(PlannedFile(row["item_id"], pages, target))
    if errors:
        raise ValueError("Manifest has problems; nothing was written:\n  " + "\n  ".join(errors))
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def apply_manifest(
    manifest_path: Path,
    layout: OneDriveLayout,
    dry_run: bool = False,
    index_engine=None,
) -> ApplyResult:
    """``index_engine``: record each filed document's text in the database too."""
    result = resolve_plan(read_manifest(manifest_path), layout)
    if dry_run or not result.filed:
        return result
    sidecar = texts_path(manifest_path)
    page_texts = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}

    entries, created_dirs = [], []
    for planned in result.filed:
        missing_dirs = []
        folder = planned.target.parent
        while not folder.exists():
            missing_dirs.append(folder)
            folder = folder.parent
        when = document_datetime(planned.target.name)
        write_pdf(planned.pages, planned.target, document_date=when)
        if when:
            set_file_times(planned.target, when)
        created_dirs.extend(str(d) for d in missing_dirs)
        entries.append({
            "item_id": planned.item_id,
            "path": str(planned.target),
            "sha256": _sha256(planned.target),
            "pages": [p.encode() for p in planned.pages],
        })
        # Persist after each file so an interrupted run can still be undone.
        result.log_path = _write_log(manifest_path, entries, created_dirs, result.log_path)
        if index_engine is not None:
            from .text_index import index_filed_document

            try:
                texts = [page_texts.get(p.encode(), "") for p in planned.pages]
                index_filed_document(index_engine, layout, planned.target, texts)
            except Exception as exc:  # filing succeeded; the index can be rebuilt later
                result.index_errors.append(f"{planned.item_id}: {exc}")
    return result


def _write_log(manifest_path: Path, entries: list, created_dirs: list, existing: Optional[Path]) -> Path:
    path = existing or unique_path(manifest_path.parent, f"apply_log_{datetime.now():%Y%m%d_%H%M%S}.json")
    path.write_text(json.dumps({
        "manifest": str(manifest_path),
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "files": entries,
        "created_dirs": created_dirs,
    }, indent=2), encoding="utf-8")
    return path


def undo_apply(log_path: Path, index_engine=None, onedrive: Optional[Path] = None) -> tuple[list[str], list[str]]:
    """Remove files written by an apply run if they are unchanged since."""
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    removed, kept = [], []
    for entry in log["files"]:
        path = Path(entry["path"])
        if path.exists() and _sha256(path) == entry["sha256"]:
            path.unlink()
            removed.append(str(path))
        else:
            kept.append(f"{path} (missing or modified since apply)")
    if index_engine is not None and onedrive is not None and removed:
        from .text_index import forget_documents

        forget_documents(index_engine, [str(Path(p).relative_to(onedrive)) for p in removed])
    for folder in sorted(map(Path, log.get("created_dirs", [])), key=lambda p: len(p.parts), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    return removed, kept
