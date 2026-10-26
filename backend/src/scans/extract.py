"""Pull filing fields (date, time, merchant, total) out of scanned text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable, Mapping, Optional, Union

from .naming import slugify

_MONTHS = {
    m: i for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
         ("dec", "december")],
        start=1,
    ) for m in names
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# (pattern, base score). Digit lookarounds instead of \b because OCR glues
# labels to values ("Date09/27/25Time09:16"); separators must repeat so
# prices like "4/5.00" are not dates.
_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})(?!\d)"), 3),
    (re.compile(r"(?<!\d)(?P<m>\d{1,2})(?P<sep>[/\-.])(?P<d>\d{1,2})(?P=sep)(?P<y>\d{4})(?!\d)"), 3),
    (re.compile(r"(?<![\d/\-.])(?P<m>\d{1,2})(?P<sep>[/\-.])(?P<d>\d{1,2})(?P=sep)(?P<yy>\d{2})(?![/\-.:]?\d)"), 2),
    (re.compile(rf"(?<![a-z])(?P<mon>{_MONTH_ALT})\.?\s*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s*(?P<y>\d{{4}})(?!\d)", re.I), 3),
    (re.compile(rf"(?<!\d)(?P<d>\d{{1,2}})\s*(?P<mon>{_MONTH_ALT})\.?,?\s*(?P<y>\d{{4}})(?!\d)", re.I), 3),
    # Glued footers: "2034661212/16/202308:10PM", "05/26/2108:22pm".
    (re.compile(r"(?P<m>\d{2})/(?P<d>\d{2})/(?P<y>20\d{2})"), 1),
    (re.compile(r"(?P<m>\d{2})/(?P<d>\d{2})/(?P<yy>\d{2})(?=\d{1,2}:\d{2})"), 1),
    # The same with one-digit month or day ("6/1/2408:12PM").
    (re.compile(r"(?<![\d/])(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<yy>\d{2})(?=\d{1,2}:\d{2})"), 1),
]
# AM/PM must sit on the same line and not start a word ("09:16\nPump",
# "10:30 Amount"), except a glued US time zone ("02:12 PMEST").
_MERIDIEM = r"(?:[ \t]*([ap])\.?[ \t]?m?\.?(?:(?![a-z])|(?=[ecmp][sd]t\b)))?"
_TIME_RE = re.compile(rf"(?<![\d:])(\d{{1,2}}):(\d{{2}})(?::\d{{2}})?{_MERIDIEM}", re.I)
_GLUED_TIME_RE = re.compile(rf"^[ \t]*(?:time:?)?[ \t]*(\d{{1,2}}):(\d{{2}})(?::\d{{2}})?{_MERIDIEM}", re.I)
_DATE_LABEL_RE = re.compile(r"\bdate\b|\btime\b|\d{1,2}:\d{2}", re.I)
_AMOUNT = r"\$?\s*(-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2})"
_TOTAL_RE = re.compile(
    rf"\b(grand\s+total|total\s+due|amount\s+due|balance\s+due|total)\b(?!\s*(?:savings|items?|tax|discount))[^\d\n$-]*{_AMOUNT}",
    re.I,
)
_HEADER_SKIP_RE = re.compile(
    r"(welcome|thank|receipt|store\s*#|tel|phone|www\.|\.com|date|order|invoice|statement|"
    r"account|deposit|customer|^\W*$|^\d[\d\s\-().#]*$)",
    re.I,
)


@dataclass
class Extracted:
    date: Optional[date] = None
    time: Optional[time] = None
    merchant: Optional[str] = None
    merchant_source: Optional[str] = None
    total: Optional[str] = None
    reasons: list[str] = field(default_factory=list)


def _build_date(parts: dict) -> Optional[date]:
    year = parts.get("y")
    if year is None and "yy" in parts:
        year = 2000 + parts["yy"] if parts["yy"] < 70 else 1900 + parts["yy"]
    month = parts.get("m") or parts.get("mon")
    try:
        return date(year, month, parts["d"])
    except (TypeError, ValueError):
        return None


def _date_candidates(text: str, today: date):
    """Yield (score, -position, date, match end) for every plausible date."""
    for pattern, base in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            parts = {k: v for k, v in match.groupdict().items() if v and k != "sep"}
            parts = {k: _MONTHS[v.lower()] if k == "mon" else int(v) for k, v in parts.items()}
            value = _build_date(parts)
            if not value or not date(1980, 1, 1) <= value <= today + timedelta(days=1):
                continue
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            line = text[line_start:line_end if line_end >= 0 else len(text)]
            score = base + (2 if _DATE_LABEL_RE.search(line) else 0)
            yield score, -match.start(), value, match.end()


def find_date_and_time(text: str, today: Optional[date] = None) -> tuple[Optional[date], Optional[time]]:
    """Pick the best-supported date, and prefer the time printed right after it.

    Ranking: explicit year forms and lines labelled with a date or time beat
    bare numbers; ties go to the earliest position on the receipt.
    """
    today = today or date.today()
    candidates = list(_date_candidates(text, today))
    if not candidates:
        return None, find_time(text)
    _score, _pos, value, end = max(candidates)
    glued = _GLUED_TIME_RE.match(text[end:end + 16])
    near = _to_time(*glued.groups()) if glued else None
    if near is None:
        # Same line as the date, then the next line ("Date:07/03/23\nTime:07:18PM").
        line_start = text.rfind("\n", 0, end) + 1
        rest = text[line_start:].split("\n", 2)
        near = find_time(rest[0]) or (find_time(rest[1]) if len(rest) > 1 else None)
    return value, near or find_time(text)


def find_date(text: str, today: Optional[date] = None) -> Optional[date]:
    return find_date_and_time(text, today)[0]


def _to_time(hour: str, minute: str, meridiem: Optional[str]) -> Optional[time]:
    hour_i, minute_i = int(hour), int(minute)
    if minute_i > 59:
        return None
    if meridiem:
        if not 1 <= hour_i <= 12:
            return None
        hour_i = hour_i % 12 + (12 if meridiem.lower() == "p" else 0)
    return time(hour_i, minute_i) if hour_i <= 23 else None


def find_time(text: str) -> Optional[time]:
    for match in _TIME_RE.finditer(text):
        value = _to_time(*match.groups())
        if value:
            return value
    return None


def find_total(text: str) -> Optional[str]:
    """Prefer the last explicit total line (subtotals appear first)."""
    matches = [m for m in _TOTAL_RE.finditer(text) if "sub" not in text[max(0, m.start() - 4):m.start()].lower()]
    if not matches:
        return None
    return matches[-1].group(2).replace(",", "").replace(" ", "")


# Words that appear on most receipts; never treat them as the merchant even if
# a past file happened to be named after one.
RECEIPT_WORDS = {
    "sale", "sales", "toast", "deposit", "parking", "receipt", "total", "cash", "credit",
    "debit", "visa", "amex", "tip", "order", "refund", "return", "purchase", "payment",
    "fee", "tax", "service", "store", "customer", "copy", "approved", "merchant", "card",
    "square", "clover", "thank_you", "welcome", "subtotal", "change", "balance",
    # Card-slip and check labels ("Terminal ID", "Server:", "Table 77").
    "terminal", "server", "table", "ticket", "station", "transaction", "reference", "approval",
    "guest", "cashier", "register",
}


# Past filenames made only of these words name the document, not the store
# ("payment_receipt", "boarding_pass").
DOCUMENT_WORDS = {
    "receipt", "receipts", "bill", "summary", "statement", "confirmation", "transaction", "recurring",
    "deposit", "packing", "list", "boarding", "pass", "payment", "order", "invoice", "refund", "checking",
    "online", "email", "ticket", "tickets", "purchase", "renewal", "registration",
}
# Single words too common to name a store on their own ("1 M COFFEE", "UNITED STATES POSTAL").
GENERIC_NAMES = {"coffee", "cafe", "market", "grill", "pizza", "bakery", "deli", "united", "american",
                 "national", "state", "city", "airport", "fresh"}


def match_known_merchant(
    text: str, known: Union[Iterable[str], Mapping[str, int]], header_lines: int = 12
) -> Optional[str]:
    """Find a merchant slug from filing history inside the text.

    ``known`` may map slugs to how often they were filed. Multi-word slugs may
    match anywhere in the header and the longest wins; single-word slugs must
    start a header line (``DUNKIN'``, ``Kroger FOR``) and the most-filed wins.
    Fuzzy matching on the first lines handles OCR noise such as
    ``CUMBERLANDFARMS`` or ``CONNIE S RESTAURANT``.
    """
    counts = known if isinstance(known, Mapping) else {m: 1 for m in known}
    known = [m for m in counts if len(m) >= 4 and m not in RECEIPT_WORDS and m not in GENERIC_NAMES
             and not set(m.split("_")) <= DOCUMENT_WORDS]
    lines = [slugify(line) for line in text.splitlines() if line.strip()][:header_lines]
    lines = [line for line in lines if line]
    header_slug = "_" + "_".join(lines) + "_"
    first_words = {line.split("_")[0] for line in lines}
    multi = [m for m in known if "_" in m and f"_{m}_" in header_slug]
    if multi:
        return max(multi, key=lambda m: (len(m), -header_slug.index(f"_{m}_")))
    single = [m for m in known if "_" not in m and m in first_words]
    if single:
        return max(single, key=lambda m: (counts[m], -header_slug.index(f"_{m}_")))
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return None
    best, best_score = None, 0.0
    for line in lines[:5]:
        squashed = line.replace("_", "")
        if len(squashed) < 5:
            continue
        for merchant in known:
            target = merchant.replace("_", "")
            if len(target) < 6:
                continue
            score = fuzz.ratio(target, squashed)
            # "nelcometodunkin": the merchant inside a short greeting line (not a
            # short line such as "RECEIPT" inside a long filename slug).
            if len(target) <= len(squashed) <= 2.5 * len(target):
                score = max(score, fuzz.partial_ratio(target, squashed) - 5)
            if score > best_score:
                best, best_score = merchant, score
    return best if best_score >= 88 else None


# Header lines that are not the store's name: an address, a city with state and
# zip, an amount, a date or weekday, a time, or mostly digits.
_NOT_A_NAME_RES = [
    re.compile(r"^\W*\d{1,5}[A-Za-z]?\s+[\w.' ]+\b(st|street|ave|avenue|rd|road|blvd|dr|drive|way|ln|lane|pl|"
               r"plaza|pkwy|parkway|hwy|highway|tpke|turnpike|sq|square|ct|court|cir|circle|route|rte)\b", re.I),
    re.compile(r"\b[A-Z]{2}[,.]?\s*\d{5}(-\d{4})?\b|,\s*(MA|NY|CA|CT|NH|RI|VT|ME|NJ|PA|NC|FL|TX|OH)\b"),
    re.compile(r"\$\s?\d|\b\d+[.,]\d{2}\b"),
    re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*day\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
               r"[a-z]*\.?\s+\d{1,2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.I),
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"\b(total|subtotal|amount|balance|pickup|track|server|table|check|guest|cashier)\b", re.I),
]


def _not_a_name(line: str) -> bool:
    digits = sum(ch.isdigit() for ch in line)
    letters = sum(ch.isalpha() for ch in line)
    return digits > letters or any(p.search(line) for p in _NOT_A_NAME_RES)


def guess_merchant_from_header(text: str, skip_names: Iterable[str] = ()) -> Optional[str]:
    skip = set(skip_names)
    for line in text.splitlines()[:8]:
        line = line.strip()
        if len(re.sub(r"[^A-Za-z]", "", line)) < 3 or _HEADER_SKIP_RE.search(line) or _not_a_name(line):
            continue
        if slugify(line) in skip or slugify(line) in RECEIPT_WORDS:
            continue
        slug = slugify(line, max_words=4)
        if slug:
            return slug
    return None


def extract_fields(
    text: str,
    known_merchants: Iterable[str] = (),
    today: Optional[date] = None,
    skip_names: Iterable[str] = (),
    phone_book=None,
    leave_out: Optional[str] = None,
) -> Extracted:
    """``skip_names`` are slugs (people's names) that are never a merchant.

    ``phone_book`` (store_directory.PhoneBook): a store recognised by its phone
    number wins over a name read from the text. ``leave_out``: evaluation only.
    """
    result = Extracted()
    if not text.strip():
        result.reasons.append("no text available (enable OCR or add a sidecar .txt)")
        return result

    result.date, result.time = find_date_and_time(text, today=today)
    result.total = find_total(text)

    merchant = match_known_merchant(text, known_merchants)
    if merchant:
        result.merchant, result.merchant_source = merchant, "history"
        result.reasons.append(f"merchant '{merchant}' matches existing OneDrive filenames")
    else:
        merchant = guess_merchant_from_header(text, skip_names)
        if merchant:
            result.merchant, result.merchant_source = merchant, "header"
            result.reasons.append(f"merchant '{merchant}' guessed from header line (new merchant)")
        else:
            result.reasons.append("merchant not found")

    found = phone_book.lookup(text, leave_out=leave_out) if phone_book is not None else None
    if found and found[0] != result.merchant:
        result.reasons = [r for r in result.reasons if not r.startswith("merchant")]
        result.merchant, result.merchant_source = found[0], "phone"
        result.reasons.append(f"merchant '{found[0]}' from the store's phone number ({found[1]} filed receipts)")

    if not result.date:
        result.reasons.append("date not found")
    return result


def parse_manifest_date(value: str) -> Optional[date]:
    value = (value or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def parse_manifest_time(value: str) -> Optional[time]:
    value = (value or "").strip().replace(":", "-")
    return datetime.strptime(value, "%H-%M").time() if value else None
