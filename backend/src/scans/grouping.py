"""Join scanned pages into whole documents.

ScanSmart saves every page separately, so a stack comes in as single pages.
A page joins the document before it when that makes sense:

- it says ``Page 2 of 3`` (``Page 1`` always starts a document);
- it is the same visit: the same total, or the same store (name or phone
  number) within three hours, as with an itemized bill and the card slip
  signed after dinner;
- the same store's letterhead continues a multi-page repair invoice;
- the open document is a manual and this page is not a dated receipt or the
  title page of a different manual (weaker when the page shares almost no
  words with the page before);
- weaker: it has no store header and no second total of its own (the bottom
  piece of a long receipt, the labor page of an invoice, a coupon or survey
  with a nearby date), it has no readable text, or a repeated letterhead
  shows another date or a second total. These joins put the document in
  review.

A page that opens with the name of a store known from filing history, or
that prints its own time of day next to a different date, starts a document
of its own even without an address under the name. Measured on date-ordered
stacks of filed documents (``evaluate-segmentation --order date``), as real
scan sessions are.

If the first page has no text (no OCR), pages are kept apart. A blank
separator sheet (``separator_sheets``) or ``keep_pdfs_whole`` always ends a
document.
Every join is explained in the manifest and can be undone by clearing
``merge_into``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional

from .classify import detect_kind, manual_slug
from .conventions import OneDriveLayout
from .cutter import ScanItem
from .extract import RECEIPT_WORDS, Extracted

# "Page 2 of 3" or "Page 2/3"; a bare "see page 3" in body text is not a marker.
_PAGE_MARKER_RE = re.compile(r"\bpage\s+(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b", re.I)
_TITLE_RE = re.compile(
    r"owner'?s\s+manual|user'?s?\s+(?:manual|guide)|operator'?s\s+manual|instruction\s+manual"
    r"|quick\s+start|assembly\s+instructions|installation\s+(?:guide|instructions)",
    re.I,
)
# A store header: phone number, "MA 01746" style state and zip, or a street address.
_HEADER_RES = [
    re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b"),
    re.compile(r"\b[A-Z]{2},?\s*\d{5}(?:-\d{4})?\b"),
    re.compile(
        r"\b\d{1,5}\s*[A-Za-z]+(?:\s+[A-Za-z]+)?\s+(?:st|street|ave|avenue|rd|road|blvd|tpke|turnpike"
        r"|hwy|highway|dr|drive|way|ln|lane|pl|plaza|pkwy|square|sq)\b",
        re.I,
    ),
]
_HEADER_LINES = 8
_STORE_NAME_LINES = 4
_MANUAL_MIN_OVERLAP = 0.1
_DOC_KINDS = {"manual", "instructions"}
_SAME_VISIT_MINUTES = 30
_SAME_STORE_MINUTES = 180
_INVOICE_DAYS = 60
_DEADLINE_DAYS = 90
_PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[\s.-]?(\d{3})[\s.-](\d{4})\b")
_GENERIC_FIRST_WORDS = RECEIPT_WORDS | {"welcome", "thank", "thanks", "your", "with", "from", "this"}


@dataclass
class Unit:
    """One scanned page (or FastFoto front/back pair) before grouping."""

    item: ScanItem
    extracted: Extracted
    kind: str
    manual: Optional[str]
    marker: Optional[int]
    title: bool
    header: bool
    phones: frozenset[str] = frozenset()

    @property
    def merchant(self) -> Optional[str]:
        # Only merchants confirmed by filing history identify a page; a header
        # guess exists for almost any line of text.
        return self.extracted.merchant if self.extracted.merchant_source in ("history", "phone") else None


@dataclass
class Group:
    units: list[Unit]
    reasons: list[str] = field(default_factory=list)
    weak: bool = False  # joined on weak evidence; needs a look

    @property
    def head(self) -> Unit:
        return self.units[0]

    def values(self, attr: str) -> list:
        return [getattr(u.extracted, attr) for u in self.units if getattr(u.extracted, attr)]

    def first(self, attr: str):
        found = self.values(attr)
        return found[0] if found else None

    @property
    def merchant(self) -> Optional[str]:
        return next((u.merchant for u in self.units if u.merchant), None)


def page_marker(text: str) -> Optional[int]:
    """Page number printed on the page (``Page 2 of 3`` -> 2)."""
    match = _PAGE_MARKER_RE.search(text)
    return int(match.group(1)) if match else None


def has_store_header(text: str) -> bool:
    """A phone number, state + zip, or street address near the top of the page."""
    top = "\n".join([line for line in text.splitlines() if line.strip()][:_HEADER_LINES])
    return any(pattern.search(top) for pattern in _HEADER_RES)


def _squashed(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", text.lower()))


def word_overlap(a: str, b: str) -> Optional[float]:
    """Share of words two pages have in common (of the shorter page's words)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return None
    return len(wa & wb) / min(len(wa), len(wb))


def names_known_store_on_top(unit: "Unit") -> bool:
    """The first lines name a store known from filing history ("Corner Cafe" with
    no address under it): the page starts a receipt of its own."""
    e = unit.extracted
    if not (e.merchant and e.merchant_source == "history"):
        return False
    top = [line for line in unit.item.text.splitlines() if line.strip()][:_STORE_NAME_LINES]
    return _squashed(e.merchant) in _squashed(" ".join(top))


def make_unit(item: ScanItem, extracted: Extracted, layout: OneDriveLayout) -> Unit:
    kind, _ = detect_kind(item.text, 1)
    return Unit(
        item=item,
        extracted=extracted,
        kind=kind,
        manual=manual_slug(layout, item.text) if kind in _DOC_KINDS else None,
        marker=page_marker(item.text),
        title=bool(_TITLE_RE.search(item.text[:600])),
        header=has_store_header(item.text),
        phones=frozenset("".join(m.groups()) for m in _PHONE_RE.finditer(item.text)),
    )


def _real_total(value: Optional[str]) -> Optional[str]:
    """Totals of 0.00 (inspection forms, balance lines) say nothing."""
    try:
        return value if value and float(value) != 0 else None
    except ValueError:
        return None


def _similar_merchant(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    first = a.split("_")[0]
    if a == b or (first == b.split("_")[0] and len(first) >= 4 and first not in _GENERIC_FIRST_WORDS):
        return True
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return False
    return fuzz.ratio(a, b) >= 85


def _minutes_apart(a: time, b: time) -> float:
    day = datetime(2000, 1, 1)
    return abs((datetime.combine(day, a) - datetime.combine(day, b)).total_seconds()) / 60


def _same_store(group: Group, unit: Unit) -> Optional[str]:
    """The store both pages come from: a repeated letterhead or phone number."""
    shared_phone = unit.phones & {p for u in group.units for p in u.phones}
    if shared_phone:
        return f"phone {min(shared_phone)}"
    names = [u.extracted.merchant for u in group.units]
    return next((n for n in names if _similar_merchant(n, unit.extracted.merchant)), None)


def _join(group: Group, unit: Unit) -> tuple[Optional[str], bool]:
    """(reason, weak) when ``unit`` continues ``group``; (None, False) to start a new document."""
    item, head = unit.item, group.head
    if item.starts_document or item.whole or head.item.whole:
        return None, False
    if not head.item.text.strip():
        return None, False  # nothing to go on (no OCR); keep pages apart
    if not item.text.strip():
        return "page has no readable text", True
    if unit.marker == 1:
        return None, False
    if unit.marker and unit.marker > 1 and not (unit.merchant and group.merchant and unit.merchant != group.merchant):
        return f"says page {unit.marker}", False

    e = unit.extracted
    if head.kind in _DOC_KINDS:
        if e.date and unit.kind == "receipt":
            return None, False
        if unit.title and unit.manual and unit.manual != head.manual:
            return None, False
        # Pages of one manual share vocabulary with the page before; a page
        # that shares almost none may be the next document in the stack.
        shared = word_overlap(group.units[-1].item.text, item.text)
        if shared is not None and shared < _MANUAL_MIN_OVERLAP:
            return f"next page of {head.kind}, but it shares few words with the page before", True
        return f"next page of {head.kind}", False

    open_date, open_time = group.first("date"), group.first("time")
    total, open_totals = _real_total(e.total), {t for t in group.values("total") if _real_total(t)}
    store = _same_store(group, unit)
    if e.date and open_date and e.date != open_date:
        days = (e.date - open_date).days
        # A repair invoice repeats its letterhead on every page and also prints
        # drop-off, estimate, or completion dates. Two separate visits would
        # each have their own total.
        if store and abs(days) <= _INVOICE_DAYS and not (total and open_totals):
            return f"same store ({store}) on the next page, with another date", True
        # A headerless page without a total that carries a nearby date is
        # usually an inside page (labor, parts) or a coupon or survey deadline.
        # A page that prints its own time of day, or opens with a known store's
        # name, is a receipt of its own (a stack is a few weeks of receipts).
        starts_own = bool(e.time) or names_known_store_on_top(unit)
        if not (unit.header or unit.title or total or starts_own) and (
            abs(days) <= _INVOICE_DAYS or 0 < days <= _DEADLINE_DAYS
        ):
            return "no store header of its own; its date is close to this document's", True
        return None, False
    gap = _minutes_apart(e.time, open_time) if e.time and open_time else None
    if gap is not None:
        if gap == 0 and (e.date or open_date):
            return "same date and time", False
        # An itemized bill and the card slip signed after dinner.
        if gap > (_SAME_STORE_MINUTES if store or total in open_totals else _SAME_VISIT_MINUTES):
            return None, False
    if total and total in open_totals:
        return f"same total ({total})", False
    if store:
        # A bill and a slip with the tip added both have totals; so do two
        # coffees bought hours apart.
        if total and open_totals and (gap is None or gap > _SAME_VISIT_MINUTES):
            return f"same store ({store}), second total", True
        return f"same store ({store})", False
    if unit.merchant and group.merchant and unit.merchant != group.merchant:
        return None, False

    # Weak evidence: the page does not look like the start of anything.
    if unit.title or unit.header or names_known_store_on_top(unit):
        return None, False
    if total and open_totals:
        return None, False  # a second total is a second purchase
    if e.date and open_date:
        return None, False  # same day, but nothing else ties them together
    return "no store header of its own", True


def group_units(units: list[Unit], enabled: bool = True) -> list[Group]:
    groups: list[Group] = []
    for unit in units:
        reason, weak = _join(groups[-1], unit) if enabled and groups else (None, False)
        if reason is None:
            groups.append(Group([unit]))
            continue
        group = groups[-1]
        group.units.append(unit)
        group.weak = group.weak or weak
        note = " (check: weak evidence)" if weak else ""
        group.reasons.append(f"joined {unit.item.item_id}: {reason}{note}")
    return groups
