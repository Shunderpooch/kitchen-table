"""Turn fetched transactions into expenses, once each, keeping the raw record.

- Every provider response is saved unmodified as a dated JSON snapshot
  (`data/fetch/<provider>/<timestamp>.json`).
- `fetched_transactions` holds each transaction once, keyed by the
  provider's id, so overlapping windows and re-runs add nothing.
- A new transaction either links to an expense already recorded from another
  source (an uploaded statement: same amount, within 3 days, similar
  description) or becomes a new expense that goes through categorization and
  the approval queue like any statement row. Two identical purchases on the
  same day stay two expenses: their provider ids differ.
- Card payments and bank deposits are recorded but are not expenses; a
  positive amount on a card is a refund (a negative expense).
- `fetch_accounts` maps each provider account to the household's card name
  ("<member> <card>") and person, and keeps its freshness: last fetch, last
  transaction, and the provider's last error.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from rapidfuzz import fuzz
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from ..db import Expense, SourceFile, StatementSource
from ..household import member_for
from ..ingest import is_uncategorized_bank_transaction
from . import simplefin

PROVIDER = "simplefin"
OVERLAP_DAYS = 5  # the Bridge's advice: re-read a few days to catch late postings
INITIAL_DAYS = 90
LINK_WINDOW_DAYS = 3
_PAYMENT_RE = re.compile(r"\b(payment|autopay|auto[\s-]?pay|thank you|pymt|epay|online pmt|transfer from)\b", re.I)
_CARD_RE = re.compile(r"card|credit|visa|amex|american express|discover|mastercard|signature|rewards", re.I)

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS fetch_accounts (
         provider TEXT NOT NULL,
         account_id TEXT NOT NULL,
         name TEXT,
         connection TEXT,
         currency TEXT,
         kind TEXT,                       -- card / bank
         card TEXT,                       -- expense card name, e.g. "Alex Amex"
         spender TEXT,
         balance REAL,
         balance_date TEXT,
         last_fetched_at TEXT,
         last_transaction_date TEXT,
         last_error TEXT,
         enabled INTEGER NOT NULL DEFAULT 1,
         PRIMARY KEY (provider, account_id)
       )""",
    """CREATE TABLE IF NOT EXISTS fetched_transactions (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         provider TEXT NOT NULL,
         account_id TEXT NOT NULL,
         txn_id TEXT NOT NULL,
         posted TEXT,
         transacted TEXT,
         amount REAL NOT NULL,
         description TEXT,
         raw TEXT NOT NULL,
         first_seen TEXT NOT NULL,
         last_seen TEXT NOT NULL,
         outcome TEXT NOT NULL,           -- expense / linked / payment / deposit / zero
         expense_id INTEGER,
         UNIQUE (provider, account_id, txn_id)
       )""",
    "CREATE INDEX IF NOT EXISTS ix_fetched_transactions_expense ON fetched_transactions (expense_id)",
]


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        for statement in _SCHEMA:
            connection.exec_driver_sql(statement)


@dataclass
class FetchReport:
    start: Optional[date] = None
    end: Optional[date] = None
    accounts: int = 0
    new_accounts: list[str] = field(default_factory=list)
    seen: int = 0
    new: int = 0
    expenses: int = 0
    linked: int = 0
    skipped: dict = field(default_factory=dict)  # outcome -> count
    errors: list[str] = field(default_factory=list)
    snapshot: Optional[Path] = None


def guess_kind(name: str) -> str:
    return "card" if _CARD_RE.search(name or "") else "bank"


def _window(engine: Engine, today: date, since: Optional[date]) -> tuple[date, date]:
    if since:
        return since, today + timedelta(days=1)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT min(last_transaction_date) FROM fetch_accounts "
            "WHERE provider = :p AND enabled = 1"), {"p": PROVIDER}).scalar()
    if rows:
        start = date.fromisoformat(rows) - timedelta(days=OVERLAP_DAYS)
    else:
        start = today - timedelta(days=INITIAL_DAYS)
    return start, today + timedelta(days=1)


def _content_hash(account_id: str, txn_id: str) -> str:
    return "fetch:" + hashlib.sha256(f"{PROVIDER}|{account_id}|{txn_id}".encode()).hexdigest()[:40]


def _link_target(session: Session, day: date, amount: Decimal, description: str, taken: set[int]) -> Optional[int]:
    """An expense from another source (an uploaded statement) for the same purchase."""
    candidates = session.execute(text(
        "SELECT e.id, e.amount, e.merchant, e.original_input_name, e.date FROM expenses e "
        "WHERE e.date BETWEEN :lo AND :hi AND abs(e.amount - :amount) < 0.005 "
        "AND coalesce(e.content_hash, '') NOT LIKE 'fetch:%' AND coalesce(e.content_hash, '') NOT LIKE 'receipt:%' "
        "AND coalesce(e.is_duplicate, 0) = 0 "
        "AND e.id NOT IN (SELECT expense_id FROM fetched_transactions WHERE expense_id IS NOT NULL)"),
        {"lo": (day - timedelta(days=LINK_WINDOW_DAYS)).isoformat(),
         "hi": (day + timedelta(days=LINK_WINDOW_DAYS)).isoformat(), "amount": float(amount)}).all()
    best, best_score = None, 0.0
    for expense_id, _, merchant, original, _ in candidates:
        if expense_id in taken:
            continue
        score = max(fuzz.token_set_ratio(description.lower(), (name or "").lower()) for name in (merchant, original))
        if score > best_score:
            best, best_score = expense_id, score
    return best if best_score >= 80 else None


def import_responses(engine: Engine, responses: list[simplefin.Response], *, now: datetime,
                     snapshot_dir: Optional[Path] = None, categorize: Optional[Callable] = None) -> FetchReport:
    """Store accounts and transactions from Bridge responses; make expenses for new ones."""
    ensure_schema(engine)
    report = FetchReport()
    if responses:
        report.start = responses[0].window.start
        report.end = responses[-1].window.end
    if snapshot_dir is not None and responses:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        report.snapshot = snapshot_dir / f"{now:%Y%m%d_%H%M%S}.json"
        report.snapshot.write_text(json.dumps(
            [{"start": r.window.start.isoformat(), "end": r.window.end.isoformat(), "response": r.body}
             for r in responses], indent=1), encoding="utf-8")

    stamp = now.isoformat(timespec="seconds")
    account_errors: dict[str, list[str]] = {}
    for response in responses:
        for error in response.body.get("errlist") or []:
            message = f"{error.get('code', 'error')}: {error.get('msg', '')}"
            if error.get("account_id"):
                account_errors.setdefault(error["account_id"], []).append(message)
            elif message not in report.errors:
                report.errors.append(message)
        for message in response.body.get("errors") or []:
            if not response.body.get("errlist") and message not in report.errors:
                report.errors.append(message)

    with Session(engine) as session:
        source_file: Optional[SourceFile] = None
        new_expenses: list[Expense] = []
        taken: set[int] = set()
        accounts_seen: dict[str, dict] = {}
        for response in responses:
            connections = {c.get("conn_id"): c.get("name") for c in response.body.get("connections") or []}
            for account in response.body.get("accounts") or []:
                account_id = str(account["id"])
                info = accounts_seen.setdefault(account_id, {"account": account, "connection": connections.get(
                    account.get("conn_id")) or (account.get("org") or {}).get("name"), "transactions": {}})
                for txn in account.get("transactions") or []:
                    info["transactions"][str(txn["id"])] = txn

        for account_id, info in accounts_seen.items():
            account = info["account"]
            name = account.get("name") or account_id
            existing = session.execute(text(
                "SELECT card, spender, kind, last_transaction_date FROM fetch_accounts "
                "WHERE provider = :p AND account_id = :a"), {"p": PROVIDER, "a": account_id}).first()
            if existing is None:
                kind = guess_kind(f"{info['connection'] or ''} {name}")
                card = f"{info['connection']} {name}".strip() if info["connection"] else name
                spender = member_for(name)
                session.execute(text(
                    "INSERT INTO fetch_accounts (provider, account_id, name, connection, currency, kind, card, spender) "
                    "VALUES (:p, :a, :n, :c, :cur, :k, :card, :s)"),
                    {"p": PROVIDER, "a": account_id, "n": name, "c": info["connection"],
                     "cur": account.get("currency"), "k": kind, "card": card, "s": spender})
                report.new_accounts.append(card)
                last_txn = None
            else:
                card, spender, kind, last_txn = existing
            report.accounts += 1

            for txn_id, txn in info["transactions"].items():
                report.seen += 1
                if txn.get("pending"):
                    continue  # only posted transactions become expenses
                known = session.execute(text(
                    "SELECT id FROM fetched_transactions WHERE provider = :p AND account_id = :a AND txn_id = :t"),
                    {"p": PROVIDER, "a": account_id, "t": txn_id}).first()
                if known:
                    session.execute(text("UPDATE fetched_transactions SET last_seen = :s WHERE id = :i"),
                                    {"s": stamp, "i": known[0]})
                    continue
                report.new += 1
                posted = simplefin.to_day(txn.get("posted"))
                day = simplefin.to_day(txn.get("transacted_at")) or posted
                raw_amount = Decimal(str(txn.get("amount", "0")))
                description = (txn.get("payee") or txn.get("description") or "").strip()
                amount = -raw_amount  # the Bridge: negative = money out; expenses: positive = spent
                expense_id = None
                if raw_amount == 0:
                    outcome = "zero"
                elif raw_amount > 0 and (_PAYMENT_RE.search(description) or kind == "bank"):
                    outcome = "payment" if kind == "card" else "deposit"
                else:
                    linked = _link_target(session, day, amount, description, taken)
                    if linked is not None:
                        outcome, expense_id = "linked", linked
                        taken.add(linked)
                        report.linked += 1
                    else:
                        outcome = "expense"
                        if source_file is None:
                            source_file = SourceFile(filename=f"SimpleFIN fetch {now:%Y-%m-%d %H:%M}",
                                                     file_type="fetch", status="completed", processed_at=now)
                            session.add(source_file)
                        expense = Expense(
                            date=day, type=description, subtype=None, merchant=description or None,
                            original_input_name=description or None, amount=amount, split=Decimal("1.0"),
                            spender=spender, card=card, source_file=source_file,
                            needs_validation=is_uncategorized_bank_transaction(description),
                            is_approved=False, content_hash=_content_hash(account_id, txn_id),
                        )
                        session.add(expense)
                        new_expenses.append(expense)
                if outcome not in ("expense", "linked"):
                    report.skipped[outcome] = report.skipped.get(outcome, 0) + 1
                session.flush()
                session.execute(text(
                    "INSERT INTO fetched_transactions (provider, account_id, txn_id, posted, transacted, amount, "
                    "description, raw, first_seen, last_seen, outcome, expense_id) VALUES (:p, :a, :t, :posted, "
                    ":transacted, :amount, :d, :raw, :s, :s, :o, :e)"),
                    {"p": PROVIDER, "a": account_id, "t": txn_id, "posted": posted.isoformat() if posted else None,
                     "transacted": day.isoformat() if day else None, "amount": float(raw_amount), "d": description,
                     "raw": json.dumps(txn), "s": stamp, "o": outcome,
                     "e": expense_id if expense_id is not None else None})
                if outcome == "expense":
                    session.execute(text(
                        "UPDATE fetched_transactions SET expense_id = :e WHERE provider = :p AND account_id = :a "
                        "AND txn_id = :t"), {"e": new_expenses[-1].id, "p": PROVIDER, "a": account_id, "t": txn_id})
                if posted and (last_txn is None or posted.isoformat() > last_txn):
                    last_txn = posted.isoformat()

            balance = account.get("balance")
            session.execute(text(
                "UPDATE fetch_accounts SET name = :n, connection = :c, balance = :b, balance_date = :bd, "
                "last_fetched_at = :s, last_transaction_date = :lt, last_error = :err "
                "WHERE provider = :p AND account_id = :a"),
                {"n": name, "c": info["connection"], "b": float(balance) if balance not in (None, "") else None,
                 "bd": simplefin.to_day(account.get("balance-date")).isoformat() if account.get("balance-date") else None,
                 "s": stamp, "lt": last_txn, "err": "; ".join(account_errors.get(account_id, [])) or None,
                 "p": PROVIDER, "a": account_id})
            if card:
                source = session.query(StatementSource).filter_by(name=card).first()
                if source is None:
                    session.add(StatementSource(name=card, source_type="credit_card" if kind == "card" else "bank_account",
                                                last_import_date=now.date()))
                else:
                    source.last_import_date = now.date()

        # Accounts with errors but no data in this response still record the error.
        for account_id, messages in account_errors.items():
            if account_id not in accounts_seen:
                session.execute(text(
                    "UPDATE fetch_accounts SET last_error = :err WHERE provider = :p AND account_id = :a"),
                    {"err": "; ".join(messages), "p": PROVIDER, "a": account_id})

        if new_expenses:
            session.flush()
            if source_file is not None:
                source_file.row_count = len(new_expenses)
            if categorize is not None:
                categorize(new_expenses, session)
        report.expenses = len(new_expenses)
        session.commit()
    return report


def run_fetch(engine: Engine, access_url: str, *, today: Optional[date] = None, since: Optional[date] = None,
              snapshot_dir: Optional[Path] = None, categorize: Optional[Callable] = None,
              fetcher: Callable = simplefin.fetch) -> FetchReport:
    ensure_schema(engine)
    today = today or date.today()
    start, end = _window(engine, today, since)
    responses = fetcher(access_url, start, end)
    return import_responses(engine, responses, now=datetime.now(), snapshot_dir=snapshot_dir, categorize=categorize)


def set_account(engine: Engine, account_id: str, *, card: Optional[str] = None, spender: Optional[str] = None,
                kind: Optional[str] = None, enabled: Optional[bool] = None) -> int:
    """Change an account's card name / person / kind; its existing expenses follow."""
    ensure_schema(engine)
    with engine.begin() as connection:
        row = connection.execute(text(
            "SELECT card, spender FROM fetch_accounts WHERE provider = :p AND account_id = :a"),
            {"p": PROVIDER, "a": account_id}).first()
        if row is None:
            raise ValueError(f"no fetched account {account_id}")
        sets = {k: v for k, v in (("card", card), ("spender", spender), ("kind", kind)) if v is not None}
        if enabled is not None:
            sets["enabled"] = int(enabled)
        if sets:
            connection.execute(text(
                f"UPDATE fetch_accounts SET {', '.join(f'{k} = :{k}' for k in sets)} "
                "WHERE provider = :p AND account_id = :a"), {**sets, "p": PROVIDER, "a": account_id})
        changed = 0
        if card is not None or spender is not None:
            changed = connection.execute(text(
                "UPDATE expenses SET card = coalesce(:card, card), spender = coalesce(:spender, spender) "
                "WHERE id IN (SELECT expense_id FROM fetched_transactions WHERE provider = :p AND account_id = :a "
                "AND outcome = 'expense')"), {"card": card, "spender": spender, "p": PROVIDER,
                                              "a": account_id}).rowcount
        return changed


def accounts(engine: Engine) -> list[dict]:
    ensure_schema(engine)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT a.account_id, a.name, a.connection, a.kind, a.card, a.spender, a.balance, a.balance_date, "
            "a.last_fetched_at, a.last_transaction_date, a.last_error, a.enabled, "
            "(SELECT count(*) FROM fetched_transactions t WHERE t.provider = a.provider "
            " AND t.account_id = a.account_id) AS transactions "
            "FROM fetch_accounts a WHERE a.provider = :p ORDER BY a.connection, a.name"), {"p": PROVIDER}).mappings()
        return [dict(r) for r in rows]
