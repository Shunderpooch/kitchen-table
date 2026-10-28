"""Decide what a scanned document is and where it belongs.

Every decision records a human-readable reason so the manifest stays
explainable. Uncertain items are routed to ``review`` instead of guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .conventions import (
    DOC_KIND_FOLDERS,
    SELF_KEY,
    OneDriveLayout,
    person_category_folder,
)
from .extract import Extracted
from .naming import build_filename, format_date, format_time, slugify

RECEIPT_CUES = [
    r"\btotal\b", r"\bsubtotal\b", r"\btax\b", r"\bchange\b", r"\bcash\b", r"\bvisa\b",
    r"\bmastercard\b", r"\bamex\b", r"\bdiscover\b", r"\bdebit\b", r"\bauth(?:orization)?\b",
    r"\bapproved\b", r"\bqty\b", r"\bbalance\b", r"\bpaid\b", r"\binvoice\b", r"\$\s?\d",
]
MANUAL_CUES = [
    r"owner'?s\s+manual", r"user'?s?\s+(?:manual|guide)", r"operator'?s\s+manual",
    r"instruction\s+manual", r"quick\s+start", r"safety\s+(?:instructions|warnings)",
    r"\bwarranty\b", r"\btroubleshooting\b", r"\bspecifications\b",
]
INSTRUCTION_CUES = [
    r"assembly\s+instructions", r"\bassembly\b", r"\binstallation\b", r"\bmounting\b",
    r"parts\s+list", r"hardware\s+(?:kit|list)", r"\bstep\s+\d", r"\btools\s+needed\b",
]
CATEGORY_KEYWORDS = {
    "gas": [r"\bunleaded\b", r"\bgallons?\b", r"\bgal\b", r"\bpump\s*#?\s*\d", r"\bfuel\b", r"\bdiesel\b"],
    "cafe": [r"\blatte\b", r"\bespresso\b", r"\bcappuccino\b", r"\bamericano\b", r"\bmocha\b", r"\bcoffee\b", r"\bcold\s+brew\b"],
    "fast_food": [r"\bdrive[\s-]?thru\b", r"\bcombo\b", r"\bnuggets\b", r"\bfries\b"],
    "restaurants": [r"\bserver\b", r"\bgratuity\b", r"\btip\b", r"\bguests?\b", r"\btable\s*#?\s*\d", r"\bentree\b", r"\bappetizer\b"],
    "grocery": [r"\bproduce\b", r"\bdeli\b", r"\bbakery\b", r"\bgrocery\b", r"\bdairy\b", r"\blb\s*@", r"\bbananas?\b"],
    "home_improvement": [r"\blumber\b", r"\bhome\s+depot\b", r"\blowe'?s\b", r"\bplywood\b", r"\bscrews?\b"],
    "health_care": [r"\bpharmacy\b", r"\brx\b", r"\bcopay\b", r"\bprescription\b", r"\bpatient\b"],
    "travel": [r"\bparking\b", r"\bgarage\b", r"\btolls?\b", r"\bhotel\b", r"\bboarding\b", r"\bairlines?\b"],
    "rental_vehicles": [r"\brental\s+agreement\b", r"\brental\s+car\b", r"\bunit\s*#.*rental\b"],
    "shipping": [r"\busps\b", r"\bfedex\b", r"\bups\s+store\b", r"\bpostage\b", r"\btracking\s*#"],
    "automotive_supplies": [r"\bmotor\s+oil\b", r"\bwiper\b", r"\bauto\s*zone\b", r"\badvance\s+auto\b"],
    "plants": [r"\bnursery\b", r"\bperennials?\b", r"\bannuals\b", r"\bmulch\b"],
    "technology": [r"\bhdmi\b", r"\busb\b", r"\bssd\b", r"\blaptop\b", r"\bbest\s+buy\b", r"\bmicro\s+center\b"],
    "clothing": [r"\bapparel\b", r"\bshirt\b", r"\bjeans\b", r"\bshoes?\b"],
}
_MODEL_RE = re.compile(r"\b(?=[A-Z0-9\-]*\d)(?=[A-Z0-9\-]*[A-Z])[A-Z0-9][A-Z0-9\-]{2,15}\b")


@dataclass
class Decision:
    kind: str = "unknown"
    person: str = SELF_KEY
    category: Optional[str] = None
    car: Optional[str] = None
    slug: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False


def _count(patterns: list[str], text: str) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


def detect_kind(text: str, page_count: int) -> tuple[str, list[str]]:
    receipt = _count(RECEIPT_CUES, text)
    manual = _count(MANUAL_CUES, text)
    instructions = _count(INSTRUCTION_CUES, text)
    if page_count > 3:
        manual += 1
    scores = {"receipt": receipt, "manual": manual, "instructions": instructions}
    kind, best = max(scores.items(), key=lambda kv: kv[1])
    if best < 2:
        return "unknown", [f"kind unclear (cues: receipt={receipt}, manual={manual}, instructions={instructions})"]
    return kind, [f"kind '{kind}' from text cues (receipt={receipt}, manual={manual}, instructions={instructions})"]


# Documents come pre-approved only when the store is filed in one folder at
# least this often (with no date tie): 90% of them then have the right date
# and folder, against 85% at 0.6 (labelled census, dev split, Oct 2026).
CONFIDENT_FOLDER_SHARE = 0.8


def detect_category(layout: OneDriveLayout, merchant: Optional[str], text: str) -> tuple[str, bool, str]:
    """Return (category, confident, reason)."""
    if merchant:
        category, count, share = layout.category_for_merchant(merchant)
        if category and category in layout.receipt_categories:
            # A store filed in several folders (a hardware store under plants
            # and home improvement) is the owner's call each time: not confident.
            confident = count >= 2 and share >= CONFIDENT_FOLDER_SHARE
            note = "" if confident or count < 2 else "; this store is filed in several folders (check)"
            return category, confident, (
                f"category '{category}' from history ({count} of {round(count / share)} '{merchant}' receipts){note}"
            )
    scores = {
        cat: _count(patterns, text)
        for cat, patterns in CATEGORY_KEYWORDS.items()
        if cat in layout.receipt_categories
    }
    if scores:
        cat, hits = max(scores.items(), key=lambda kv: kv[1])
        runner_up = sorted(scores.values())[-2] if len(scores) > 1 else 0
        if hits >= 1:
            return cat, hits >= 2 and hits > runner_up, f"category '{cat}' from keywords ({hits} hits)"
    fallback = "general" if "general" in layout.receipt_categories else layout.receipt_categories[0]
    return fallback, False, f"category defaulted to '{fallback}'"


def suggest_people(layout: OneDriveLayout, text: str) -> list[str]:
    return [
        key for key, person in layout.people.items()
        if any(p.search(text) for p in person.name_patterns())
    ]


def find_car(layout: OneDriveLayout, text: str, leave_out: Optional[str] = None) -> tuple[Optional[str], str]:
    """(car folder, reason): the model named in the text, else identifiers (VIN,
    plate, policy number) seen before in one car's folder."""
    car = detect_car(layout, text)
    if car:
        return car, f"mentions car '{car}'"
    book = getattr(layout, "car_book", None)
    found = book.lookup(text, leave_out=leave_out) if book is not None else None
    if found and found[0] in layout.cars:
        return found[0], f"car '{found[0]}' from identifiers on its earlier paperwork ({found[1]} matched)"
    return None, ""


def detect_car(layout: OneDriveLayout, text: str) -> Optional[str]:
    text_slug = "_" + slugify(text) + "_"
    hits = []
    for key, car in layout.cars.items():
        phrase = "_".join(car.model_tokens)
        if not phrase or f"_{phrase}_" not in text_slug:
            continue
        if f"_{car.make}_" in text_slug or len(phrase) >= 5:
            hits.append(key)
    return hits[0] if len(hits) == 1 else None


def manual_slug(layout: OneDriveLayout, text: str) -> Optional[str]:
    """``brand_model`` for manuals, e.g. ``dewalt_dcs334``."""
    words = set(slugify(text[:2000]).split("_"))
    present = [b for b in layout.manual_brands if b in words]
    brand = max(present, key=lambda b: layout.manual_brands[b]) if present else None
    model = next((m.group(0) for m in _MODEL_RE.finditer(text[:2000])), None)
    parts = [p for p in (brand, slugify(model) if model else None) if p]
    return "_".join(parts) or None


def decide(
    layout: OneDriveLayout,
    extracted: Extracted,
    text: str,
    page_count: int,
    batch_person: str = SELF_KEY,
    forced_kind: Optional[str] = None,
    leave_out_car: Optional[str] = None,  # evaluation: this document's own car folder
) -> Decision:
    decision = Decision(person=batch_person)
    if forced_kind:
        decision.kind = forced_kind
        decision.reasons.append(f"kind '{forced_kind}' set for batch")
    else:
        decision.kind, reasons = detect_kind(text, page_count)
        decision.reasons += reasons

    mentioned = suggest_people(layout, text)
    if mentioned and batch_person not in mentioned:
        decision.reasons.append(
            f"text mentions {', '.join(mentioned)}; batch person is '{batch_person}' (check person)"
        )
        decision.needs_review = True

    if decision.kind == "receipt":
        decision.slug = extracted.merchant
        decision.category, confident, reason = detect_category(layout, extracted.merchant, text)
        decision.reasons.append(reason)
        if not confident:
            decision.needs_review = True
        car, car_reason = find_car(layout, text, leave_out_car)
        if car:
            decision.car = car
            decision.reasons.append(f"{car_reason} (filed under cars/; clear 'car' to undo)")
            decision.needs_review = True
        if not extracted.date or not extracted.merchant:
            decision.needs_review = True
        if extracted.date_rivals:
            decision.reasons.append("another date on the receipt is just as likely (check the date)")
            decision.needs_review = True
        if not extracted.time:
            decision.reasons.append("time not found (filename will omit it)")
            decision.needs_review = True
        if extracted.merchant_source == "header":
            decision.needs_review = True
    elif decision.kind in ("manual", "instructions"):
        decision.slug = manual_slug(layout, text)
        decision.reasons.append(f"manual name '{decision.slug or '?'}' from brand/model in text")
        decision.needs_review = True
    else:
        decision.slug = extracted.merchant
        decision.needs_review = True
        car, car_reason = find_car(layout, text, leave_out_car)
        if car:
            decision.car = car
            decision.reasons.append(f"{car_reason} (filed under cars/; clear 'car' to undo)")
    return decision


def destination_folder(
    layout: OneDriveLayout,
    kind: str,
    person: str,
    category: Optional[str],
    car: Optional[str],
    doc_date: Optional[str],
) -> Path:
    """Map manifest fields to a folder that follows the OneDrive layout."""
    person_obj = layout.people.get(person) if person != SELF_KEY else None
    if person != SELF_KEY and person_obj is None:
        raise ValueError(f"Unknown person '{person}'")

    if kind not in DOC_KIND_FOLDERS:
        # Receipts, and unknown documents that still need review, file as receipts.
        category = category or "general"
        if car:
            if car not in layout.cars:
                raise ValueError(f"Unknown car folder '{car}'")
            return layout.cars[car].path
        if person_obj:
            return person_category_folder(person_obj, category)
        return layout.receipts / category

    folder_name = DOC_KIND_FOLDERS[kind]
    if person_obj:
        folder = _person_doc_folder(person_obj.path, folder_name)
    else:
        folder = layout.documents / folder_name
    if kind == "tax" and doc_date:
        folder = folder / doc_date[:4]
    return folder


def _person_doc_folder(person_dir: Path, folder_name: str) -> Path:
    """Reuse ``documents/<folder>`` style folders a person already has."""
    docs = next(
        (person_dir / d for d in ("documents", "Documents") if (person_dir / d).is_dir()),
        person_dir,
    )
    for candidate in (docs, person_dir):
        for sub in candidate.iterdir():
            if sub.is_dir() and slugify(sub.name) == folder_name:
                return sub
    return docs / folder_name


def filename_for(kind: str, doc_date: Optional[str], doc_time: Optional[str], slug: Optional[str]) -> str:
    if kind in ("manual", "instructions"):
        return build_filename(None, None, slug)
    return build_filename(doc_date, doc_time, slug)


def describe(extracted: Extracted) -> tuple[Optional[str], Optional[str]]:
    return format_date(extracted.date), format_time(extracted.time)
