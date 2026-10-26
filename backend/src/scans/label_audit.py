"""Find filing labels that are probably wrong.

Two kinds of evidence:

- **Labels against labels** (no OCR needed): names that break the
  convention, impossible dates, spelling variants of one merchant, a
  merchant filed outside the category it almost always uses, and the same
  file saved twice.
- **Labels against the text**: the date or time in the filename appears
  nowhere in the document while the text clearly shows another one, or the
  merchant's name is absent while a different known merchant heads the
  page. Only clear contradictions are reported: a document whose text has
  no date at all says nothing about its label.

Findings are stored in ``label_findings`` with room for a reviewer's
verdict after looking at the page image, and for the owner's decision.
Nothing is renamed or moved here.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..db.models import LabelFinding, ScannedDocument
from .conventions import OneDriveLayout
from .extract import _TIME_RE, _date_candidates, _to_time, find_date_and_time, match_known_merchant
from .naming import merchant_of, parse_filename, slugify
from .segmentation_eval import Doc

CHECKS = (
    "name_format", "implausible_date", "undated_receipt", "merchant_variant", "category_outlier",
    "duplicate_content", "text_date", "text_time", "text_merchant",
)

_DASHED_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})_(.+)$")
_UNDERSCORE_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})_(.+)$")
_US_DATE_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})[_\s-]+(.+)$")


@dataclass
class Finding:
    path: str
    check: str
    label: str
    evidence: str
    suggestion: str
    strength: float  # 0-1, for ranking; not a probability


def _key(layout: OneDriveLayout, path: Path) -> str:
    return str(path.relative_to(layout.onedrive)).replace("\\", "/")


def _receipt_roots(layout: OneDriveLayout) -> list[Path]:
    roots = [layout.receipts] + [p.receipts_root for p in layout.people.values() if p.receipts_root_exists]
    return [r for r in roots if r.is_dir()]


# ------------------------------------------------------------- labels vs labels


def _name_format(name: str) -> Optional[str]:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    for pattern, build in (
        (_DASHED_TIME_RE, lambda m: f"{m[1]}_{m[2]}-{m[3]}_{m[4]}"),
        (_UNDERSCORE_TIME_RE, lambda m: f"{m[1]}_{m[2]}-{m[3]}_{m[4]}"),
        (_US_DATE_RE, lambda m: f"{m[3]}-{m[1]}-{m[2]}_{slugify(m[4])}"),
    ):
        match = pattern.match(stem)
        if match:
            return build(match) + ext
    fixed = slugify(stem)
    parsed = parse_filename(stem)
    if parsed.date and fixed and fixed != stem.lower() and not re.search(r"\(\d+\)$", stem):
        return fixed + ext
    return None


def _squash(slug: str) -> str:
    return slug.replace("_", "").replace("-", "")


def merchant_variants(merchants: dict[str, int]) -> dict[str, tuple[str, float]]:
    """Rare slug -> (more common spelling, strength)."""
    from rapidfuzz import fuzz

    result: dict[str, tuple[str, float]] = {}
    slugs = sorted(merchants, key=lambda m: -merchants[m])
    by_squash: dict[str, str] = {}
    for slug in slugs:
        squashed = _squash(slug)
        if squashed in by_squash:
            result[slug] = (by_squash[squashed], 0.9)
        else:
            by_squash[squashed] = slug
    for i, common in enumerate(slugs):
        if len(common) < 6:
            continue
        for rare in slugs[i + 1:]:
            if rare in result or len(rare) < 6 or merchants[rare] > merchants[common]:
                continue
            if re.sub(r"\d", "", rare) == re.sub(r"\d", "", common):
                continue  # store numbers: cvs_1234 vs cvs_5678
            score = fuzz.ratio(_squash(common), _squash(rare))
            if score >= 90:
                # A tie in use means history cannot say which spelling is right.
                tie = merchants[rare] == merchants[common]
                result[rare] = (common, 0.3 if tie else round((score - 80) / 20, 3))
    return result


def label_findings(layout: OneDriveLayout, today: Optional[date] = None) -> list[Finding]:
    today = today or date.today()
    findings: list[Finding] = []
    receipt_files = [p for root in _receipt_roots(layout) for p in root.rglob("*.pdf")]

    for path in receipt_files:
        key, parsed = _key(layout, path), parse_filename(path.name)
        fixed = _name_format(path.name)
        if fixed:
            findings.append(Finding(key, "name_format", path.name, "name does not follow YYYY-MM-DD[_HH-MM]_merchant",
                                    fixed, 0.8))
        if parsed.date and len(parsed.date) == 10:
            try:
                value = date.fromisoformat(parsed.date)
            except ValueError:
                findings.append(Finding(key, "implausible_date", parsed.date, "not a real calendar date", "", 0.9))
                continue
            if value > today:
                findings.append(Finding(key, "implausible_date", parsed.date, "date is in the future", "", 0.9))
            elif value.year < 1990:
                findings.append(Finding(key, "implausible_date", parsed.date, "unusually old for a receipt", "", 0.4))
        elif not parsed.date and not fixed:
            findings.append(Finding(key, "undated_receipt", path.name, "receipt name has no date", "", 0.3))

    counts = {m: sum(c.values()) for m, c in layout.merchant_categories.items()}
    variants = merchant_variants(counts)
    misformatted = {f.path for f in findings if f.check == "name_format"}
    for path in receipt_files:
        merchant = merchant_of(parse_filename(path.name))
        if merchant in variants and _key(layout, path) not in misformatted:
            common, strength = variants[merchant]
            findings.append(Finding(
                _key(layout, path), "merchant_variant", merchant,
                f"'{common}' is used {counts[common]} times, '{merchant}' {counts[merchant]}",
                path.name.replace(merchant, common), strength,
            ))

    for root in _receipt_roots(layout):
        for path in root.rglob("*.pdf"):
            if path.parent == root:
                continue
            merchant = merchant_of(parse_filename(path.name))
            history = layout.merchant_categories.get(merchant) if merchant else None
            if not history:
                continue
            category = slugify(path.relative_to(root).parts[0])
            top, top_n = history.most_common(1)[0]
            total = sum(history.values())
            share = top_n / total
            if top != category and total >= 4 and share >= 0.75:
                findings.append(Finding(
                    _key(layout, path), "category_outlier", category,
                    f"{top_n} of {total} '{merchant}' receipts are in '{top}'", top, round(share, 3),
                ))
    return findings


def duplicate_findings(engine: Engine) -> list[Finding]:
    """Identical files (same sha256) at more than one path."""
    groups: dict[str, list[str]] = defaultdict(list)
    with Session(engine) as session:
        for path, digest in session.execute(select(ScannedDocument.path, ScannedDocument.sha256)):
            if digest:
                groups[digest].append(path)
    findings = []
    for paths in groups.values():
        if len(paths) > 1:
            for path in sorted(paths)[1:]:
                findings.append(Finding(path, "duplicate_content", path, f"identical to {sorted(paths)[0]}",
                                        "delete this copy", 0.95))
    return findings


# --------------------------------------------------------------- labels vs text


def _relation(label: date, seen: date) -> str:
    if label.year == seen.year and label.month == seen.day and label.day == seen.month:
        return "day and month swapped"
    if (label.month, label.day) == (seen.month, seen.day):
        return "year differs"
    if label.year == seen.year and label.month == seen.month:
        return f"{abs(label.day - seen.day)} day(s) apart"
    if label.year == seen.year and label.day == seen.day:
        return "month differs"
    return ""


def _times_in(text: str) -> list:
    times = []
    for match in _TIME_RE.finditer(text):
        value = _to_time(*match.groups())
        if value:
            times.append(value)
    return times


def text_findings(layout: OneDriveLayout, docs: Iterable[Doc], today: Optional[date] = None) -> list[Finding]:
    from .naming_eval import held_out, labels_for

    today = today or date.today()
    findings = []
    for doc in docs:
        text = "\n".join(doc.pages)
        if not text.strip():
            continue
        labels = labels_for(layout, doc.key)
        if labels.date:
            label_date = date.fromisoformat(labels.date)
            dates = {value for _s, _p, value, _e in _date_candidates(text, today)}
            if dates and label_date not in dates:
                best, _ = find_date_and_time(text, today)
                relation = _relation(label_date, best) if best else ""
                strength = 0.9 if relation else 0.6 if len(dates) == 1 else 0.4
                findings.append(Finding(
                    doc.key, "text_date", labels.date,
                    f"text shows {', '.join(sorted(d.isoformat() for d in dates)[:4])}; best {best}"
                    + (f" ({relation})" if relation else ""),
                    best.isoformat() if best else "", strength,
                ))
            if labels.time and label_date in dates:
                label_time = datetime.strptime(labels.time, "%H-%M").time()
                times = _times_in(text)
                if times and label_time not in times:
                    _, best_time = find_date_and_time(text, today)
                    near = min(times, key=lambda t: abs(t.hour * 60 + t.minute - label_time.hour * 60 - label_time.minute))
                    gap = abs(near.hour * 60 + near.minute - label_time.hour * 60 - label_time.minute)
                    twelve = gap == 720
                    findings.append(Finding(
                        doc.key, "text_time", labels.time,
                        f"text shows {', '.join(sorted({t.strftime('%H:%M') for t in times})[:5])}"
                        + (" (12 hours apart: AM/PM)" if twelve else f"; nearest is {gap} min away"),
                        (best_time or near).strftime("%H-%M"), 0.8 if twelve or len(times) == 1 else 0.4,
                    ))
        if labels.merchant:
            tokens = [t for t in labels.merchant.split("_") if len(t) >= 4 and not t.isdigit()]
            squashed = _squash(slugify(text))
            if tokens and not any(t in squashed for t in tokens):
                with held_out(layout, labels, doc.key):
                    other = match_known_merchant(text, layout.known_merchants())
                if other and other != labels.merchant and not set(other.split("_")) & set(labels.merchant.split("_")):
                    findings.append(Finding(doc.key, "text_merchant", labels.merchant,
                                            f"name not in the text; the page is headed by known merchant '{other}'",
                                            other, 0.5))
    return findings


def vote_on_variants(findings: list[Finding], texts: dict[str, list[str]]) -> None:
    """Let a document's own text pick between two spellings of its merchant."""
    for finding in findings:
        if finding.check != "merchant_variant" or finding.path not in texts:
            continue
        common = finding.evidence.split("'")[1]  # "'<common>' is used N times, ..."
        squashed = _squash(slugify("\n".join(texts[finding.path])))
        has_label, has_common = _squash(finding.label) in squashed, _squash(common) in squashed
        if has_label != has_common:
            winner = finding.label if has_label else common
            finding.evidence += f"; the receipt text spells it '{winner}'"
            finding.strength = 0.95 if winner == common else 0.1


# --------------------------------------------------------------------- storage


def save_findings(engine: Engine, findings: list[Finding]) -> tuple[int, int]:
    """Upsert by (path, check), keeping any verdicts and decisions already recorded."""
    LabelFinding.__table__.create(engine, checkfirst=True)
    added = updated = 0
    with Session(engine) as session:
        existing = {(f.path, f.check): f for f in session.scalars(select(LabelFinding))}
        for finding in findings:
            row = existing.get((finding.path, finding.check))
            if row is None:
                row = LabelFinding(path=finding.path, check=finding.check)
                session.add(row)
                added += 1
            else:
                updated += 1
            row.label, row.evidence = finding.label, finding.evidence
            row.suggestion, row.strength = finding.suggestion, finding.strength
        session.commit()
    return added, updated


def summarize(findings: list[Finding]) -> Counter:
    return Counter(f.check for f in findings)


# ----------------------------------------------------------------------- review

VERDICTS = ("label_wrong", "text_wrong", "both_wrong", "unclear")


def review_sample(engine: Engine, checks: Iterable[str], per_check: int, seed: int = 7) -> list[LabelFinding]:
    """A simple random sample of unreviewed findings for each check (stratified by check)."""
    import random

    rng = random.Random(seed)
    LabelFinding.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        chosen = []
        for check in checks:
            rows = list(session.scalars(
                select(LabelFinding).where(LabelFinding.check == check, LabelFinding.reviewer_verdict.is_(None))
                .order_by(LabelFinding.id)
            ))
            chosen += rng.sample(rows, min(per_check, len(rows)))
        session.expunge_all()
    return chosen


def render_pages(layout: OneDriveLayout, path: str, out_dir: Path, stem: str, max_pages: int = 2,
                 max_side: int = 1400) -> list[Path]:
    """PNG images of a document's first pages, for looking at the evidence."""
    from pypdf import PdfReader

    from .cutter import PageRef, load_page_image

    full = layout.onedrive / path
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for page in range(1, min(max_pages, len(PdfReader(str(full)).pages)) + 1):
        image = load_page_image(PageRef(full, page))
        if image is None:
            continue
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        target = out_dir / f"{stem}_p{page}.png"
        image.save(target)
        written.append(target)
    return written


def record_verdict(engine: Engine, finding_id: int, verdict: str, note: str, by: str) -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    with Session(engine) as session:
        row = session.get(LabelFinding, finding_id)
        if row is None:
            raise ValueError(f"no finding {finding_id}")
        row.reviewer_verdict, row.reviewer_note, row.reviewed_by = verdict, note, by
        session.commit()


def audit_report(engine: Engine) -> dict[str, dict]:
    """Per check: findings, reviewed, share where the label was wrong (Wilson CI), and the implied count."""
    from .naming_eval import wilson

    LabelFinding.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        rows = list(session.scalars(select(LabelFinding)))
    report = {}
    for check in sorted({r.check for r in rows}):
        of_check = [r for r in rows if r.check == check]
        reviewed = [r for r in of_check if r.reviewer_verdict and r.reviewer_verdict != "unclear"]
        wrong = sum(r.reviewer_verdict in ("label_wrong", "both_wrong") for r in reviewed)
        rate = wilson(wrong, len(reviewed))
        report[check] = {
            "findings": len(of_check), "reviewed": len(reviewed),
            "unclear": sum(r.reviewer_verdict == "unclear" for r in of_check),
            "label_wrong_rate": rate,
            "estimated_wrong_labels": tuple(round(x * len(of_check)) for x in rate) if reviewed else None,
            "text_wrong": sum(r.reviewer_verdict == "text_wrong" for r in reviewed),
        }
    return report


def export_audit_xlsx(engine: Engine, out: Path) -> int:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    LabelFinding.__table__.create(engine, checkfirst=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "findings"
    columns = ["id", "check", "strength", "path", "label", "evidence", "suggestion",
               "reviewer_verdict", "reviewer_note", "reviewed_by", "user_decision"]
    sheet.append(columns)
    with Session(engine) as session:
        rows = session.scalars(select(LabelFinding).order_by(LabelFinding.check, LabelFinding.strength.desc()))
        count = 0
        for row in rows:
            sheet.append([getattr(row, c) if c != "strength" else float(row.strength or 0) for c in columns])
            count += 1
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip("ABCDEFGHIJK", [6, 18, 9, 70, 30, 70, 45, 14, 50, 12, 14]):
        sheet.column_dimensions[column].width = width

    summary = workbook.create_sheet("summary")
    summary.append(["check", "findings", "reviewed", "label wrong (share)", "95% CI low", "95% CI high",
                    "estimated wrong labels", "text wrong", "unclear"])
    for check, r in audit_report(engine).items():
        p, lo, hi = r["label_wrong_rate"]
        est = r["estimated_wrong_labels"]
        summary.append([check, r["findings"], r["reviewed"], p if r["reviewed"] else None,
                        lo if r["reviewed"] else None, hi if r["reviewed"] else None,
                        f"{est[0]} ({est[1]}-{est[2]})" if est else None, r["text_wrong"], r["unclear"]])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(out)
    return count
