"""Score naming and filing against every labelled document in OneDrive.

Each filed document is labelled by where and how it was filed:

- the filename gives the date, time, and merchant (``YYYY-MM-DD[_HH-MM]_merchant``);
- the folder gives the category (``receipts/<category>``), the person
  (``people/<key>``), the car (``cars/<car>``), or the kind (manuals,
  hardware instructions).

Every indexed document is predicted from its stored text and compared with
those labels. Predictions are held out: the document's own filing is removed
from the merchant, category, and manual-brand history before predicting, so
a merchant filed only once must be rediscovered from the text.

Reported per field: accuracy with a 95% Wilson interval, split into wrong
and missing (abstained); category per-class precision/recall/F1, macro-F1
and a confusion matrix; merchant accuracy for previously seen vs new
merchants; and the end-to-end operating point: how many documents would be
filed without review (coverage), and how many of those get exactly the right
filename in the right folder (precision).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from .classify import decide, describe, destination_folder, filename_for
from .conventions import SELF_KEY, OneDriveLayout
from .extract import extract_fields
from .naming import merchant_of, parse_filename, slugify
from .segmentation_eval import Doc


@dataclass
class Labels:
    kind: str  # receipt / car / manual / instructions
    person: str
    category: Optional[str]  # receipts category folder, if any
    car: Optional[str]
    date: Optional[str]
    time: Optional[str]
    merchant: Optional[str]
    folder: str  # parent folder, relative to OneDrive, lower-case


def labels_for(layout: OneDriveLayout, key: str) -> Labels:
    parts = key.split("/")
    lowered = [p.lower() for p in parts]
    parsed = parse_filename(parts[-1])
    person = SELF_KEY
    category = car = None
    if lowered[0] == "people" and len(parts) > 2:
        person = parts[1]
        if "receipts" in lowered:
            rest = parts[lowered.index("receipts") + 1:-1]
            category = slugify(rest[0]) if rest else None
        kind = "receipt"
    elif "cars" in lowered:
        kind = "car"
        car = parts[lowered.index("cars") + 1] if len(parts) > lowered.index("cars") + 2 else None
    elif "manuals" in lowered:
        kind = "manual"
    elif "hardware_instructions" in lowered:
        kind = "instructions"
    else:
        kind = "receipt"
        rest = parts[lowered.index("receipts") + 1:-1] if "receipts" in lowered else []
        category = slugify(rest[0]) if rest else None
    dated = bool(parsed.date and len(parsed.date) == 10)
    folder = "/".join(parts[:-1])
    if kind == "car":
        # Filing routes to the car's folder; subfolders inside it are the owner's choice.
        cars_at = lowered.index("cars")
        folder = "/".join(parts[:cars_at + 2] if car else parts[:cars_at + 1])
    return Labels(
        kind=kind, person=person, category=category, car=car,
        date=parsed.date if dated else None,
        time=parsed.time if dated else None,
        # Car paperwork and manuals are named by description, not by merchant.
        merchant=merchant_of(parsed) if dated and kind == "receipt" else None,
        folder=folder.lower(),
    )


@contextmanager
def held_out(layout: OneDriveLayout, labels: Labels, key: str):
    """Remove this document's own filing from history while it is predicted."""
    merchant = labels.merchant
    saved = layout.merchant_categories.get(merchant) if merchant else None
    if saved is not None and labels.category and saved.get(labels.category):
        reduced = saved.copy()
        reduced[labels.category] -= 1
        reduced = +reduced  # drop zero counts
        if reduced:
            layout.merchant_categories[merchant] = reduced
        else:
            del layout.merchant_categories[merchant]
    brand = slugify(Path(key).stem).split("_")[0] if labels.kind in ("manual", "instructions") else None
    saved_brand = layout.manual_brands.get(brand) if brand else None
    if saved_brand:
        layout.manual_brands[brand] = saved_brand - 1
        if not layout.manual_brands[brand]:
            del layout.manual_brands[brand]
    try:
        yield
    finally:
        if saved is not None:
            layout.merchant_categories[merchant] = saved
        if saved_brand:
            layout.manual_brands[brand] = saved_brand


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return 0.0, 0.0, 0.0
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class FieldScore:
    correct: int = 0
    wrong: int = 0
    missing: int = 0

    @property
    def n(self) -> int:
        return self.correct + self.wrong + self.missing

    def add(self, predicted, truth) -> None:
        if predicted in (None, ""):
            self.missing += 1
        elif predicted == truth:
            self.correct += 1
        else:
            self.wrong += 1

    def summary(self) -> dict:
        return {"n": self.n, "accuracy": wilson(self.correct, self.n),
                "wrong": self.wrong / self.n if self.n else 0.0,
                "missing": self.missing / self.n if self.n else 0.0}


@dataclass
class NamingReport:
    documents: int = 0
    fields: dict[str, FieldScore] = field(default_factory=lambda: defaultdict(FieldScore))
    category_confusion: Counter = field(default_factory=Counter)  # (truth, predicted) -> n
    auto_filed: int = 0
    auto_filed_exact: int = 0
    reviewed_exact: int = 0
    by_folder: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    misses: list[dict] = field(default_factory=list)

    def category_table(self) -> tuple[dict[str, dict], float]:
        classes = sorted({t for t, _ in self.category_confusion} | {p for _, p in self.category_confusion})
        table = {}
        for c in classes:
            tp = self.category_confusion[(c, c)]
            fp = sum(n for (t, p), n in self.category_confusion.items() if p == c and t != c)
            fn = sum(n for (t, p), n in self.category_confusion.items() if t == c and p != c)
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            table[c] = {"support": tp + fn, "precision": prec, "recall": rec,
                        "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0}
        supported = [row["f1"] for row in table.values() if row["support"]]
        return table, sum(supported) / len(supported) if supported else 0.0


def evaluate_naming(
    layout: OneDriveLayout,
    docs: list[Doc],
    today: Optional[date] = None,
    keep_misses: int = 200,
) -> NamingReport:
    report = NamingReport()
    names = layout.name_slugs()
    for doc in docs:
        labels = labels_for(layout, doc.key)
        text = "\n".join(doc.pages)
        if not text.strip():
            continue
        with held_out(layout, labels, doc.key):
            known = layout.known_merchants()
            seen_before = bool(labels.merchant and known.get(labels.merchant))
            extracted = extract_fields(text, known, today=today, skip_names=names,
                                       phone_book=layout.phone_book, leave_out=labels.merchant)
            decision = decide(layout, extracted, text, len(doc.pages), labels.person, leave_out_car=labels.car)
            doc_date, doc_time = describe(extracted)
            try:
                folder = destination_folder(layout, decision.kind, decision.person, decision.category,
                                            decision.car, doc_date)
                predicted_folder = str(folder.relative_to(layout.onedrive)).replace("\\", "/").lower()
            except ValueError:
                predicted_folder = ""
            predicted_name = filename_for(decision.kind, doc_date, doc_time, decision.slug)

        report.documents += 1
        f = report.fields
        predicted_kind = "car" if decision.car else (
            decision.kind if decision.kind in ("manual", "instructions") else "receipt")
        f["kind"].add(predicted_kind, labels.kind)
        if labels.date:
            f["date"].add(doc_date, labels.date)
            if labels.time:
                f["time"].add(doc_time, labels.time)
        if labels.merchant:
            f["merchant"].add(decision.slug, labels.merchant)
            f["merchant (seen before)" if seen_before else "merchant (new)"].add(decision.slug, labels.merchant)
        if labels.category:
            f["category"].add(decision.category, labels.category)
            report.category_confusion[(labels.category, decision.category or "(none)")] += 1
        if labels.car:
            f["car"].add(decision.car, labels.car)
        f["folder"].add(predicted_folder, labels.folder)
        f["folder (" + labels.kind + ")"].add(predicted_folder, labels.folder)
        # "_2" only separates same-name receipts; apply adds it again when needed.
        actual_name = re.sub(r"[_-][2-9](\.pdf)$", r"\1", Path(doc.key).name.lower())
        exact = predicted_folder == labels.folder and predicted_name.lower() == actual_name
        if labels.kind != "car":  # car paperwork names are free-form descriptions
            f["filename + folder"].add(exact, True)
        bucket = report.by_folder[labels.kind if labels.kind != "receipt" else (labels.category or "receipt")]
        bucket["n"] += 1
        bucket["exact"] += exact
        if decision.needs_review:
            report.reviewed_exact += exact
        else:
            report.auto_filed += 1
            report.auto_filed_exact += exact
            bucket["auto_filed"] += 1
            bucket["auto_filed_exact"] += exact
        if not exact and len(report.misses) < keep_misses:
            report.misses.append({
                "file": doc.key, "predicted": f"{predicted_folder}/{predicted_name}",
                "action": "review" if decision.needs_review else "file",
            })
    return report
