"""Learn the OneDrive filing layout from what is already on disk.

Nothing here is hard-coded beyond folder names that already exist in the
user's OneDrive. Merchant -> category knowledge comes from existing receipt
filenames, so suggestions stay deterministic and explainable.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..household import self_name
from .naming import merchant_of, parse_filename, slugify

SELF_KEY = "self"
# The OneDrive owner; their documents live directly under Documents/.
SELF_ALIASES = {"self", "me", "mine", ""}

# Document kinds that file into a single Documents/<folder>.
DOC_KIND_FOLDERS = {
    "manual": "manuals",
    "instructions": "hardware_instructions",
    "housing": "housing",
    "medical": "medical_records",
    "travel": "travel",
    "gift": "gifts",
    "tax": "taxes",
}

# Slugs that describe a document rather than a merchant.
GENERIC_SLUGS = {
    "bill", "receipt", "receipts", "invoice", "order", "payment", "statement",
    "unnamed_scan", "scan", "document", "copy", "check", "dinner", "lunch",
}

# Leading words of manual filenames that describe the item, not the brand.
GENERIC_MANUAL_WORDS = {
    "adhesive", "appliance", "bathroom", "bed", "card", "charging", "convenience",
    "end", "frame", "installation", "laptop", "leg", "monitor", "mounting", "multi",
    "old", "piano", "fitted", "rainfall", "manual", "instructions", "user", "guide",
}
_BRAND_RE = re.compile(r"^[a-z]{3,}(?:-[a-z]+)?$")
_CAR_FOLDER_RE =re.compile(r"^(?P<year>\d{4})_(?P<rest>[a-z0-9_\-]+)$")
_CAR_GENERIC_TOKENS = {
    "ev", "lt", "ls", "se", "ss", "z28", "premier", "performance", "custom",
    "convertible", "unlimited", "grand", "touring", "redline", "spyder", "aero",
    "v8", "4xe", "sahara", "ultra",
}


@dataclass
class Person:
    """A supported person with a folder under ``OneDrive/people``."""

    key: str
    path: Path
    first: str
    last: str
    receipts_root: Path
    receipts_root_exists: bool

    @property
    def display_name(self) -> str:
        return f"{self.first.title()} {self.last.replace('-', ' ').title()}"

    def name_patterns(self) -> list[re.Pattern]:
        first = re.escape(self.first)
        last = re.escape(self.last).replace(r"\-", r"[\s\-]")
        return [
            re.compile(rf"\b{first}\s+(?:[a-z]\.?\s+)?{last}\b", re.IGNORECASE),
            re.compile(rf"\b{last},\s*{first}\b", re.IGNORECASE),
        ]


@dataclass
class Car:
    key: str
    path: Path
    year: str
    make: str
    model_tokens: list[str]


@dataclass
class OneDriveLayout:
    onedrive: Path
    documents: Path
    receipts: Path
    receipt_categories: list[str]
    merchant_categories: dict[str, Counter] = field(default_factory=dict)
    people: dict[str, Person] = field(default_factory=dict)
    cars: dict[str, Car] = field(default_factory=dict)
    manual_brands: Counter = field(default_factory=Counter)
    # store_directory.PhoneBook / CarBook, when the database is available (attach_phone_book).
    phone_book: Optional[object] = None
    car_book: Optional[object] = None

    def doc_folder(self, kind: str) -> Path:
        return self.documents / DOC_KIND_FOLDERS[kind]

    def known_merchants(self) -> dict[str, int]:
        """Merchant slug -> number of filed receipts, excluding people's names."""
        names = self.name_slugs()
        return {
            m: sum(c.values()) for m, c in self.merchant_categories.items() if m not in names
        }

    def name_slugs(self) -> set[str]:
        """Slugs of people's names, which must never be taken for a merchant."""
        names = {"_".join(self_name()), "_".join(reversed(self_name()))}
        for person in self.people.values():
            names |= {f"{person.first}_{person.last}", f"{person.last}_{person.first}"}
        return names

    def category_for_merchant(self, merchant: str) -> tuple[Optional[str], int, float]:
        """Return (category, occurrences, share) from filing history."""
        counts = self.merchant_categories.get(merchant)
        if not counts:
            return None, 0, 0.0
        category, count = counts.most_common(1)[0]
        return category, count, count / sum(counts.values())

    def resolve_person(self, query: Optional[str]) -> str:
        """Resolve a CLI/manifest person value into ``self`` or a people folder key."""
        value = slugify(query or "")
        if value in SELF_ALIASES:
            return SELF_KEY
        if value in self.people:
            return value
        matches = [
            key for key, person in self.people.items()
            if value in {person.first, f"{person.first}_{person.last}", person.last}
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(
                f"Unknown person '{query}'. Use 'self' or one of: {', '.join(sorted(self.people))}"
            )
        raise ValueError(f"Ambiguous person '{query}': {', '.join(sorted(matches))}")


def default_onedrive_root() -> Path:
    for var in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return Path.home() / "OneDrive"


def _iter_files(root: Path):
    if not root.exists():
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def _learn_merchants(receipts_root: Path, into: dict[str, Counter]) -> None:
    """Count merchant slugs per category folder under a receipts root."""
    for path in _iter_files(receipts_root):
        if path.parent == receipts_root:
            continue
        category = slugify(path.relative_to(receipts_root).parts[0])
        parsed = parse_filename(path.name)
        # Only dated names follow the convention; raw scanner output such as
        # ScanSmart's "Document_20261018_0001.pdf" must not become a merchant.
        merchant = merchant_of(parsed)
        if merchant and merchant not in GENERIC_SLUGS and len(merchant) >= 3:
            into.setdefault(merchant, Counter())[category] += 1


def _find_receipts_root(person_dir: Path) -> tuple[Path, bool]:
    for candidate in ("receipts", "documents/receipts", "Documents/receipts"):
        path = person_dir / candidate
        if path.is_dir():
            return path, True
    for docs in ("documents", "Documents"):
        if (person_dir / docs).is_dir():
            return person_dir / docs / "receipts", False
    return person_dir / "receipts", False


def _learn_people(people_root: Path) -> dict[str, Person]:
    people: dict[str, Person] = {}
    if not people_root.is_dir():
        return people
    for entry in sorted(people_root.iterdir()):
        if not entry.is_dir() or "_" not in entry.name:
            continue
        last, first = entry.name.rsplit("_", 1)
        receipts_root, exists = _find_receipts_root(entry)
        people[entry.name] = Person(
            key=entry.name,
            path=entry,
            first=first.lower(),
            last=last.lower(),
            receipts_root=receipts_root,
            receipts_root_exists=exists,
        )
    return people


def _learn_cars(cars_root: Path) -> dict[str, Car]:
    cars: dict[str, Car] = {}
    if not cars_root.is_dir():
        return cars
    for entry in sorted(cars_root.iterdir()):
        match = _CAR_FOLDER_RE.match(entry.name)
        if not entry.is_dir() or not match:
            continue
        tokens = match.group("rest").split("_")
        cars[entry.name] = Car(
            key=entry.name,
            path=entry,
            year=match.group("year"),
            make=tokens[0],
            model_tokens=[t for t in tokens[1:] if t not in _CAR_GENERIC_TOKENS] or tokens[1:],
        )
    return cars


def learn_layout(onedrive: Optional[Path] = None) -> OneDriveLayout:
    onedrive = Path(onedrive) if onedrive else default_onedrive_root()
    documents = onedrive / "Documents"
    receipts = documents / "receipts"
    if not receipts.is_dir():
        raise FileNotFoundError(f"No receipts folder found at {receipts}")

    categories = sorted(p.name for p in receipts.iterdir() if p.is_dir())
    merchants: dict[str, Counter] = defaultdict(Counter)
    _learn_merchants(receipts, merchants)

    people = _learn_people(onedrive / "people")
    for person in people.values():
        if person.receipts_root_exists:
            _learn_merchants(person.receipts_root, merchants)

    manual_brands: Counter = Counter()
    for kind in ("manual", "instructions"):
        folder = documents / DOC_KIND_FOLDERS[kind]
        if folder.is_dir():
            for path in folder.iterdir():
                brand = slugify(path.stem).split("_")[0]
                if _BRAND_RE.match(brand) and brand not in GENERIC_MANUAL_WORDS:
                    manual_brands[brand] += 1

    return OneDriveLayout(
        onedrive=onedrive,
        documents=documents,
        receipts=receipts,
        receipt_categories=categories,
        merchant_categories=dict(merchants),
        people=people,
        cars=_learn_cars(documents / "cars"),
        manual_brands=manual_brands,
    )


def person_category_folder(person: Person, category: str) -> Path:
    """Where a person's receipt of ``category`` belongs.

    Reuse an existing category folder even when it is spelled differently
    (``fast food`` vs ``fast_food``). Flat receipts folders stay flat.
    """
    root = person.receipts_root
    if not root.is_dir():
        return root / category
    subdirs = [p for p in root.iterdir() if p.is_dir()]
    for subdir in subdirs:
        if slugify(subdir.name) == category:
            return subdir
    has_flat_files = any(p.is_file() for p in root.iterdir())
    if has_flat_files and not subdirs:
        return root
    return root / category
