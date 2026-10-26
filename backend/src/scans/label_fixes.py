"""Apply decided label fixes: rename or move files in OneDrive, with an undo log.

A finding is applied only when its ``user_decision`` is ``fix``. The fix is
built from the finding's ``suggestion``:

- ``name_format`` / ``merchant_variant``: the suggested filename;
- ``category_outlier``: move into the suggested category folder;
- ``duplicate_content``: move the extra copy to ``Documents/_duplicates``
  (nothing is deleted);
- ``text_date`` / ``implausible_date`` / ``undated_receipt``: the date part
  becomes the suggestion (``YYYY-MM-DD``); an undated name may instead get
  a whole new filename;
- ``text_time``: the time part becomes the suggestion (``HH-MM``);
- ``text_merchant``: the merchant part becomes the suggestion.

Several fixes for one file combine into one rename. Nothing is overwritten:
a target that already exists is reported and skipped. Renamed files keep
their stored text (the index path is updated) and get file times matching
the new name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..db.models import LabelFinding, ScannedDocument
from .conventions import OneDriveLayout
from .filedates import document_datetime, set_file_times
from .naming import build_filename, parse_filename, slugify

DUPLICATES_FOLDER = "Documents/_duplicates"


@dataclass
class PlannedFix:
    source: str  # relative to OneDrive
    target: str
    finding_ids: list[int] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    problem: Optional[str] = None


def _receipt_category_root(rel: str) -> Optional[tuple[str, str]]:
    """(path up to and including 'receipts', current category) for a receipt path."""
    parts = rel.split("/")
    lowered = [p.lower() for p in parts]
    if "receipts" not in lowered:
        return None
    at = lowered.index("receipts")
    if len(parts) - at < 3:
        return None  # file sits directly in receipts/
    return "/".join(parts[:at + 1]), parts[at + 1]


_PREFIX_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<time>[_-]\d{2}[-.]\d{2}(?=[_\-\s.]))?")


def _apply_fields(name: str, date_: Optional[str], time_: Optional[str], merchant: Optional[str]) -> str:
    """Change date/time in place (keeps free-form names such as car paperwork intact);
    a new merchant rebuilds the name from its parts."""
    if merchant:
        parsed = parse_filename(name)
        return build_filename(date_ or parsed.date, time_ or parsed.time, merchant, Path(name).suffix.lower())
    match = _PREFIX_RE.match(name)
    if not match:
        raise ValueError("name does not start with a date")
    prefix = date_ or match["date"]
    if time_:
        prefix += f"_{time_}"
    elif match["time"]:
        prefix += "_" + match["time"][1:].replace(".", "-")
    return prefix + name[match.end():]


def plan_fixes(engine: Engine, layout: OneDriveLayout) -> list[PlannedFix]:
    with Session(engine) as session:
        rows = list(session.scalars(
            select(LabelFinding).where(LabelFinding.user_decision == "fix").order_by(LabelFinding.path)
        ))
    by_path: dict[str, list[LabelFinding]] = {}
    for row in rows:
        by_path.setdefault(row.path, []).append(row)

    plans = []
    for rel, findings in by_path.items():
        plan = PlannedFix(rel, rel, [f.id for f in findings], [f.check for f in findings])
        folder, name = rel.rsplit("/", 1)
        new_date = new_time = new_merchant = None
        try:
            for f in sorted(findings, key=lambda f: f.check != "name_format"):  # format first
                value = (f.suggestion or "").strip()
                if f.check in ("name_format", "merchant_variant"):
                    if not value:
                        raise ValueError(f"{f.check} has no suggested name")
                    name = value if f.check == "name_format" else _apply_fields(
                        name, None, None, merchant_of_name(value))
                elif f.check == "category_outlier":
                    root = _receipt_category_root(rel)
                    if not root or slugify(value) != value:
                        raise ValueError("category move needs a receipt in a category folder")
                    folder = f"{root[0]}/{value}"
                elif f.check == "duplicate_content":
                    folder = f"{DUPLICATES_FOLDER}/{folder}"
                elif f.check == "undated_receipt" and value.lower().endswith(".pdf"):
                    if "/" in value or "\\" in value:
                        raise ValueError("suggested name must be a bare filename")
                    name = value  # a whole new name, e.g. from a date in another format
                elif f.check in ("text_date", "implausible_date", "undated_receipt"):
                    datetime.strptime(value, "%Y-%m-%d")
                    new_date = value
                elif f.check == "text_time":
                    datetime.strptime(value, "%H-%M")
                    new_time = value
                elif f.check == "text_merchant":
                    if slugify(value) != value or not value:
                        raise ValueError("merchant must be a slug")
                    new_merchant = value
            if new_date or new_time or new_merchant:
                if parse_filename(name).date is None and not new_date:
                    raise ValueError("cannot set a time or merchant on an undated name")
                if parse_filename(name).date is None:
                    name = build_filename(new_date, new_time, slugify(Path(name).stem), Path(name).suffix.lower())
                else:
                    name = _apply_fields(name, new_date, new_time, new_merchant)
        except ValueError as exc:
            plan.problem = str(exc)
        plan.target = f"{folder}/{name}"
        if plan.target == rel and not plan.problem:
            plan.problem = "nothing to change"
        elif not plan.problem and (layout.onedrive / plan.target).exists():
            plan.problem = f"{plan.target} already exists"
        plans.append(plan)
    # Two fixes must not land on the same name.
    seen: dict[str, str] = {}
    for plan in plans:
        if plan.problem:
            continue
        if plan.target.lower() in seen:
            plan.problem = f"same target as {seen[plan.target.lower()]}"
        seen[plan.target.lower()] = plan.source
    return plans


def merchant_of_name(filename: str) -> Optional[str]:
    return parse_filename(filename).slug


@dataclass
class FixResult:
    applied: list[PlannedFix] = field(default_factory=list)
    skipped: list[PlannedFix] = field(default_factory=list)
    log_path: Optional[Path] = None


def apply_fixes(engine: Engine, layout: OneDriveLayout, log_dir: Path, dry_run: bool = True) -> FixResult:
    result = FixResult()
    plans = plan_fixes(engine, layout)
    result.skipped = [p for p in plans if p.problem]
    todo = [p for p in plans if not p.problem]
    if dry_run:
        result.applied = todo
        return result

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"label_fixes_{datetime.now():%Y%m%d_%H%M%S}.json"
    entries = []
    for plan in todo:
        source, target = layout.onedrive / plan.source, layout.onedrive / plan.target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():  # re-check right before renaming
                raise FileExistsError(f"{plan.target} already exists")
            os.rename(source, target)  # never overwrites on Windows; same volume
        except OSError as exc:
            plan.problem = str(exc)
            result.skipped.append(plan)
            continue
        when = document_datetime(target.name)
        if when:
            try:
                set_file_times(target, when)
            except OSError:
                pass
        entries.append({"from": plan.source, "to": plan.target, "findings": plan.finding_ids})
        log_path.write_text(json.dumps({"applied_at": datetime.now().isoformat(timespec="seconds"),
                                        "renames": entries}, indent=2), encoding="utf-8")
        _move_records(engine, plan.source, plan.target, plan.finding_ids)
        result.applied.append(plan)
    result.log_path = log_path if entries else None
    return result


def _move_records(engine: Engine, source: str, target: str, finding_ids: list[int]) -> None:
    with Session(engine) as session:
        doc = session.scalar(select(ScannedDocument).where(ScannedDocument.path == source))
        if doc is not None:
            doc.path = target
            doc.folder = target.rsplit("/", 1)[0]
            parsed = parse_filename(target.rsplit("/", 1)[1])
            doc.doc_date = datetime.strptime(parsed.date, "%Y-%m-%d").date() if parsed.date and len(parsed.date) == 10 else None
            doc.doc_time = parsed.time.replace("-", ":") if parsed.time else None
            doc.merchant = parsed.slug
        for row in session.scalars(select(LabelFinding).where(LabelFinding.path == source)):
            row.path = target
            if row.id in finding_ids:
                row.user_decision = "fixed"
        session.commit()
    relink_expenses(engine, source, target)


def relink_expenses(engine: Engine, source: str, target: str) -> int:
    """Expenses link receipts by filename; keep those links pointing at the renamed file."""
    old_name, new_name = source.rsplit("/", 1)[-1], target.rsplit("/", 1)[-1]
    if old_name == new_name:
        return 0
    with engine.begin() as connection:
        tables = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        if "expenses" not in tables:
            return 0
        result = connection.exec_driver_sql(
            "UPDATE expenses SET link = ? WHERE link = ?", (new_name, old_name)
        )
        return result.rowcount or 0


def undo_fixes(engine: Engine, layout: OneDriveLayout, log_path: Path) -> tuple[list[str], list[str]]:
    log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    undone, problems = [], []
    for entry in reversed(log["renames"]):
        source, target = layout.onedrive / entry["to"], layout.onedrive / entry["from"]
        if not source.exists() or target.exists():
            problems.append(f"{entry['to']}: cannot move back")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, target)
        when = document_datetime(target.name)
        if when:
            set_file_times(target, when)
        _move_records(engine, entry["to"], entry["from"], [])
        with Session(engine) as session:
            for row in session.scalars(select(LabelFinding).where(LabelFinding.id.in_(entry["findings"]))):
                row.user_decision = "fix"
            session.commit()
        undone.append(entry["from"])
    return undone, problems
