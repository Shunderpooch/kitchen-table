"""Statement archive and monthly reconciliation.

Statements stay the record of what was charged, even when transactions are
fetched automatically (connections break, aggregators miss rows):

- `archive_statement` keeps one copy of every statement file, by content
  hash, under `data/statements/<card>/<closing date>_<hash>.<ext>`, with its
  period and rows (`statements`, `statement_rows`). The same file twice
  changes nothing.
- `reconcile_rows` matches each statement row to the expense already
  recorded for it (fetched, or from an earlier import): same card when known,
  same amount, a few days apart (posting vs purchase dates), similar
  description; one row per expense. Unmatched rows are what the fetch missed.
- `statement_report` / `coverage` say, per statement and per card and month,
  whether everything on the statement is accounted for and whether fetched
  transactions in the period are missing from the statement.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz
from sqlalchemy import Engine, text

MATCH_DAYS = 4  # statements print posting dates; fetches may carry purchase dates
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS statements (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         card TEXT,
         period_start TEXT,
         period_end TEXT,
         original_name TEXT NOT NULL,
         archived_path TEXT NOT NULL,
         sha256 TEXT NOT NULL UNIQUE,
         rows INTEGER NOT NULL DEFAULT 0,
         charges REAL NOT NULL DEFAULT 0,
         credits REAL NOT NULL DEFAULT 0,
         archived_at TEXT NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS statement_rows (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         statement_id INTEGER NOT NULL REFERENCES statements(id),
         row_date TEXT NOT NULL,
         description TEXT,
         amount REAL NOT NULL,          -- positive = charge, negative = credit
         status TEXT NOT NULL,          -- matched / created
         expense_id INTEGER
       )""",
    "CREATE INDEX IF NOT EXISTS ix_statement_rows_statement ON statement_rows (statement_id)",
    "CREATE INDEX IF NOT EXISTS ix_statement_rows_expense ON statement_rows (expense_id)",
]


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in _SCHEMA:
            connection.exec_driver_sql(statement)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "unknown").lower()).strip("_") or "unknown"


@dataclass
class Row:
    """One statement line, as an expense would record it (positive = charge)."""
    day: date
    description: str
    amount: Decimal


@dataclass
class RowMatch:
    row: Row
    expense_id: Optional[int]  # None: not recorded anywhere yet
    spending: bool = True  # False: a balance transfer, never an expense

    @property
    def status(self) -> str:
        return "matched" if self.expense_id else ("created" if self.spending else "not spending")


# Moving a balance between cards is not spending.
NOT_SPENDING_RE = re.compile(r"\bbalance transfer\b|\btransfer (?:to|from) (?:savings|checking)\b", re.I)


@dataclass
class ArchiveResult:
    statement_id: int
    already_archived: bool
    archived_path: Path
    matches: list[RowMatch] = field(default_factory=list)


UNKNOWN_CARDS = {"unknown", "bank", ""}


def _card_of_matches(engine: Engine, matches: list["RowMatch"]) -> Optional[str]:
    """The card most matched rows were recorded on (a statement covers one card)."""
    ids = [m.expense_id for m in matches if m.expense_id]
    if not ids:
        return None
    with engine.connect() as connection:
        cards = [r[0] for r in connection.execute(text(
            f"SELECT card FROM expenses WHERE id IN ({','.join(str(int(i)) for i in ids)}) AND card IS NOT NULL"))]
    if not cards:
        return None
    best = max(set(cards), key=cards.count)
    return best if cards.count(best) >= 0.6 * len(cards) else None


def name_similarity(statement_text: str, recorded: Optional[str]) -> float:
    """A statement line ("GOOGLE *YouTubePremium g.co/helppay# CA") against a recorded
    name, which may be a cleaned slug ("youtube_premium")."""
    if not recorded:
        return 0.0
    a, b = statement_text.lower(), recorded.lower().replace("_", " ")
    score = fuzz.token_set_ratio(a, b)
    squashed = re.sub(r"[^a-z0-9]", "", b)
    if len(squashed) >= 5 and squashed in re.sub(r"[^a-z0-9]", "", a):
        score = max(score, 90)
    return score


def same_card(a: str, b: str) -> bool:
    """"Amex" (read from a file) and "Alex Amex" (as recorded) are the same card;
    "Alex Amex" and "Sam Amex" are not."""
    words_a, words_b = set(_slug(a).split("_")), set(_slug(b).split("_"))
    return words_a <= words_b or words_b <= words_a


def reconcile_rows(engine: Engine, card: Optional[str], rows: list[Row]) -> list[RowMatch]:
    """Each row's existing expense (one-to-one), or None."""
    matches: list[RowMatch] = []
    taken: set[int] = set()
    rows_to_match = [r for r in rows if not NOT_SPENDING_RE.search(r.description)]
    matches.extend(RowMatch(r, None, spending=False) for r in rows if NOT_SPENDING_RE.search(r.description))
    rows = rows_to_match
    with engine.connect() as connection:
        claimed = {r[0] for r in connection.execute(text(
            "SELECT expense_id FROM statement_rows WHERE expense_id IS NOT NULL"))} if _has_table(engine, "statement_rows") else set()
        for row in sorted(rows, key=lambda r: r.day):
            candidates = connection.execute(text(
                "SELECT id, card, merchant, original_input_name, date FROM expenses "
                "WHERE date BETWEEN :lo AND :hi AND abs(amount - :amount) < 0.005 "
                "AND coalesce(is_duplicate, 0) = 0 AND coalesce(content_hash, '') NOT LIKE 'receipt:%'"),
                {"lo": (row.day - timedelta(days=MATCH_DAYS)).isoformat(),
                 "hi": (row.day + timedelta(days=MATCH_DAYS)).isoformat(), "amount": float(row.amount)}).all()
            best, best_score = None, 0.0
            for expense_id, expense_card, merchant, original, day in candidates:
                if expense_id in taken or expense_id in claimed:
                    continue
                if card and expense_card and not same_card(card, expense_card):
                    continue
                similarity = max(name_similarity(row.description, name) for name in (merchant, original))
                days_apart = abs((date.fromisoformat(str(day)[:10]) - row.day).days)
                same = bool(card and expense_card and same_card(card, expense_card))
                # A recorded name may be a cleaned slug ("youtube_premium"): same card,
                # amount, and day is the same charge even when the words differ.
                score = similarity - 5 * days_apart + (20 if same else 0) + (15 if same and days_apart == 0 else 0)
                if score > best_score:
                    best, best_score = expense_id, score
            # Same card and amount within days is strong; a weak description match needs the card.
            if best is not None and best_score >= 55:
                taken.add(best)
                matches.append(RowMatch(row, best))
            else:
                matches.append(RowMatch(row, None))
    return matches


def _has_table(engine: Engine, name: str) -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
                                       {"n": name}).first())


def statement_period(engine: Engine, card: Optional[str], days: list[date],
                     closing: Optional[date]) -> tuple[Optional[date], Optional[date]]:
    """The billing cycle: ends on the closing date (else the last row), starts the day
    after the card's previous statement (else a month before), never after the first row."""
    if not days and not closing:
        return None, None
    end = closing or max(days)
    previous = None
    if card:
        with engine.connect() as connection:
            previous = connection.execute(text(
                "SELECT max(period_end) FROM statements WHERE card = :c AND period_end < :e"),
                {"c": card, "e": end.isoformat()}).scalar()
    if previous:
        start = date.fromisoformat(previous) + timedelta(days=1)
    else:
        month_before = (end.replace(day=1) - timedelta(days=1))
        start = month_before.replace(day=min(end.day, month_before.day)) + timedelta(days=1)
    if days:
        start = min(start, min(days))
    return start, end


def archive_statement(engine: Engine, path: Path, card: Optional[str], rows: list[Row], archive_root: Path,
                      now: Optional[datetime] = None, closing: Optional[date] = None) -> ArchiveResult:
    """Keep the file and its rows once; return each row's existing expense (if any)."""
    ensure_schema(engine)
    path = Path(path)
    digest = file_sha256(path)
    with engine.connect() as connection:
        existing = connection.execute(text("SELECT id, archived_path FROM statements WHERE sha256 = :s"),
                                      {"s": digest}).first()
    if existing:
        return ArchiveResult(existing[0], True, Path(existing[1]))
    if card and card.strip().lower() in UNKNOWN_CARDS:
        card = None  # read from a file that did not say: match on any card, then name it from the matches
    matches = reconcile_rows(engine, card, rows)
    recorded_card = _card_of_matches(engine, matches)
    if card is None or (recorded_card and same_card(card, recorded_card)
                        and len(_slug(recorded_card).split("_")) > len(_slug(card).split("_"))):
        card = recorded_card or card  # "Discover" read from the file, "Sam Discover" as recorded
    period_start, period_end = statement_period(engine, card, [r.day for r in rows], closing)
    folder = archive_root / _slug(card or "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{period_end or 'undated'}_{digest[:8]}{path.suffix.lower()}"
    shutil.copy2(path, target)
    charges =sum(float(r.amount) for r in rows if r.amount > 0)
    credits = sum(-float(r.amount) for r in rows if r.amount < 0)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO statements (card, period_start, period_end, original_name, archived_path, sha256, rows, "
            "charges, credits, archived_at) VALUES (:c, :ps, :pe, :o, :a, :s, :n, :ch, :cr, :t)"),
            {"c": card, "ps": period_start.isoformat() if period_start else None,
             "pe": period_end.isoformat() if period_end else None, "o": path.name, "a": str(target), "s": digest,
             "n": len(rows), "ch": round(charges, 2), "cr": round(credits, 2),
             "t": (now or datetime.now()).isoformat(timespec="seconds")})
        statement_id = connection.execute(text("SELECT last_insert_rowid()")).scalar()
        for match in matches:
            connection.execute(text(
                "INSERT INTO statement_rows (statement_id, row_date, description, amount, status, expense_id) "
                "VALUES (:s, :d, :desc, :a, :st, :e)"),
                {"s": statement_id, "d": match.row.day.isoformat(), "desc": match.row.description,
                 "a": float(match.row.amount), "st": match.status,
                 "e": match.expense_id})
    return ArchiveResult(statement_id, False, target, matches)


def record_created(engine: Engine, statement_id: int, expense_ids: list[Optional[int]]) -> None:
    """After import: link the rows that became new expenses (same order as the unmatched rows)."""
    with engine.begin() as connection:
        rows = connection.execute(text(
            "SELECT id FROM statement_rows WHERE statement_id = :s AND status = 'created' ORDER BY id"),
            {"s": statement_id}).all()
        for (row_id,), expense_id in zip(rows, expense_ids):
            connection.execute(text("UPDATE statement_rows SET expense_id = :e WHERE id = :r"),
                               {"e": expense_id, "r": row_id})


def _fetched_cards(connection) -> set[str]:
    tables = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    if "fetch_accounts" not in tables:
        return set()
    return {r[0] for r in connection.exec_driver_sql("SELECT card FROM fetch_accounts WHERE card IS NOT NULL")}


def statement_report(engine: Engine, statement_id: int) -> dict:
    """Rows matched, rows the fetch missed, fetched transactions missing from the statement."""
    ensure_schema(engine)
    with engine.connect() as connection:
        statement = connection.execute(text("SELECT * FROM statements WHERE id = :s"), {"s": statement_id}).mappings().one()
        rows = connection.execute(text(
            "SELECT r.row_date, r.description, r.amount, r.status, r.expense_id, e.content_hash FROM statement_rows r "
            "LEFT JOIN expenses e ON e.id = r.expense_id WHERE r.statement_id = :s ORDER BY r.row_date"),
            {"s": statement_id}).mappings().all()
        fetched = statement["card"] in _fetched_cards(connection)
        extra = []
        if fetched and statement["period_start"]:
            extra = [dict(r) for r in connection.execute(text(
                "SELECT e.id, e.date, e.merchant, e.amount FROM expenses e "
                "WHERE e.card = :c AND e.date BETWEEN :lo AND :hi AND coalesce(e.content_hash, '') LIKE 'fetch:%' "
                "AND e.id NOT IN (SELECT expense_id FROM statement_rows WHERE expense_id IS NOT NULL) "
                "ORDER BY e.date"), {"c": statement["card"], "lo": statement["period_start"],
                                    "hi": statement["period_end"]}).mappings()]
    missing = [dict(r) for r in rows if r["status"] == "created"] if fetched else []
    matched = sum(1 for r in rows if r["status"] == "matched")
    return {
        "statement": dict(statement), "fetched_card": fetched, "rows": len(rows), "matched": matched,
        "missing_from_fetch": missing, "missing_from_statement": extra,
        "reconciled": bool(rows) and not missing and not extra if fetched else None,
    }


def coverage(engine: Engine, months: int = 12, today: Optional[date] = None) -> dict:
    """Per card and month (by statement closing date): statement on file, reconciled or not."""
    ensure_schema(engine)
    today = today or date.today()
    start = date(today.year, today.month, 1)
    month_keys = []
    for _ in range(months):
        month_keys.append(f"{start:%Y-%m}")
        start = (start - timedelta(days=1)).replace(day=1)
    month_keys.reverse()
    with engine.connect() as connection:
        statements = connection.execute(text(
            "SELECT id, card, period_end FROM statements WHERE period_end IS NOT NULL")).all()
        cards = sorted({s[1] for s in statements if s[1]} | _fetched_cards(connection))
    grid: dict[str, dict[str, str]] = {card: {m: "" for m in month_keys} for card in cards}
    for statement_id, card, period_end in statements:
        month = period_end[:7]
        if card in grid and month in grid[card]:
            report = statement_report(engine, statement_id)
            status = ("reconciled" if report["reconciled"] else "gaps") if report["fetched_card"] else "on file"
            grid[card][month] = status
    return {"months": month_keys, "cards": grid}
