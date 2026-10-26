"""Stores recognised by their phone number, and cars by their identifiers,
learned from filed documents.

A store's logo often does not survive OCR, but its phone number is printed
the same way on every receipt ("88 Oak St. / Riverton, MA / 555-014-2142"
is Dairy Queen). Filed receipts give phone -> merchant (from the filename);
a phone seen on at least two receipts of mostly one merchant names the store.

Measured on the labelled census (dev split, each receipt left out of its own
lookup): 62 merchant names fixed, 5 broken, of 1,882 (Oct 2026).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})\b")
MIN_RECEIPTS = 2
MIN_SHARE = 0.8


def phones_in(text: str) -> set[str]:
    return {"".join(m.groups()) for m in PHONE_RE.finditer(text)}


@dataclass
class PhoneBook:
    merchants_by_phone: dict[str, Counter] = field(default_factory=dict)

    def add(self, merchant: str, text: str) -> None:
        for phone in phones_in(text):
            self.merchants_by_phone.setdefault(phone, Counter())[merchant] += 1

    def lookup(self, text: str, leave_out: Optional[str] = None) -> Optional[tuple[str, int]]:
        """(merchant, receipts) for the phone numbers in ``text``. ``leave_out``:
        this receipt's own merchant, already counted in the book (evaluation)."""
        votes: Counter = Counter()
        for phone in phones_in(text):
            counts = self.merchants_by_phone.get(phone)
            if not counts:
                continue
            if leave_out:
                counts = counts.copy()
                counts[leave_out] -= 1
                counts = +counts
            total = sum(counts.values())
            if total < MIN_RECEIPTS:
                continue
            merchant, n = counts.most_common(1)[0]
            if n / total >= MIN_SHARE:
                votes[merchant] += n
        return votes.most_common(1)[0] if votes else None


# Identifiers on car paperwork: VINs, plates, policy and account numbers
# (letters and digits mixed, or 7+ digits).
CAR_ID_RE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{5,}\b|\b\d{7,}\b")
CAR_MIN_DOCUMENTS = 2
CAR_MIN_SHARE = 0.9


def car_ids_in(text: str) -> set[str]:
    return {t.replace("-", "") for t in CAR_ID_RE.findall(text.upper())}


@dataclass
class CarBook:
    """Which car a document is about, from identifiers seen in that car's folder.

    Measured on the labelled census (dev split, leave-one-out): right car for
    539 of 1,049 car documents (model name alone: 156), 33 wrong (Oct 2026).
    """

    cars_by_id: dict[str, Counter] = field(default_factory=dict)

    def add(self, car: str, text: str) -> None:
        for token in car_ids_in(text):
            self.cars_by_id.setdefault(token, Counter())[car] += 1

    def lookup(self, text: str, leave_out: Optional[str] = None) -> Optional[tuple[str, int]]:
        """(car folder, identifiers matched); ``leave_out``: evaluation only."""
        votes: Counter = Counter()
        for token in car_ids_in(text):
            counts = self.cars_by_id.get(token)
            if not counts:
                continue
            if leave_out:
                counts = counts.copy()
                counts[leave_out] -= 1
                counts = +counts
            total = sum(counts.values())
            if total < CAR_MIN_DOCUMENTS:
                continue
            car, n = counts.most_common(1)[0]
            if n / total >= CAR_MIN_SHARE:
                votes[car] += 1
        return votes.most_common(1)[0] if votes else None


def car_of_path(path: str) -> Optional[str]:
    """The car folder a filed document is in (``Documents/cars/<car>/...``)."""
    parts = path.split("/")
    lowered = [p.lower() for p in parts]
    if "cars" in lowered and len(parts) > lowered.index("cars") + 2:
        return parts[lowered.index("cars") + 1]
    return None


def attach_phone_book(layout, engine=None):
    """Give ``layout`` the phone and car books from the database (phones from
    receipts whose filename names a merchant; identifiers from car folders);
    naming works without them."""
    try:
        if engine is None:
            from ..db.session import get_engine

            engine = get_engine()
        layout.phone_book, layout.car_book = load_books(engine)
    except Exception:  # no database (yet): stores and cars are found from the text alone
        layout.phone_book = layout.car_book = None
    return layout


def load_books(engine) -> tuple[PhoneBook, CarBook]:
    from sqlalchemy import text as sql

    from .naming import merchant_of, parse_filename

    phones, cars = PhoneBook(), CarBook()
    with engine.connect() as connection:
        rows = connection.execute(sql(
            "SELECT d.path, d.kind, group_concat(p.text, char(10)) FROM scanned_documents d "
            "JOIN scanned_pages p ON p.document_id = d.id GROUP BY d.id")).all()
    for path, kind, text in rows:
        if not text:
            continue
        car = car_of_path(path)
        if car:
            cars.add(car, text)
        elif kind == "receipt":
            parsed = parse_filename(Path(path).name)
            merchant = merchant_of(parsed) if parsed.date else None
            if merchant:
                phones.add(merchant, text)
    return phones, cars


