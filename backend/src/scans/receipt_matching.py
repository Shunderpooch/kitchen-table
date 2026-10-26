"""Match scanned receipts to statement expenses.

Signals, from strongest:

- the expense amount is printed on the receipt (ideally as the amount paid),
  or the receipt total plus a tip written on the slip;
- the card: receipts often print the card's last four digits, and which
  digits belong to which statement card is learned from strong matches;
- the dates are close (statements post a few days later; bookings are
  charged ahead). A longer gap is accepted only when the store and exact
  amount both agree, as with bills and reservations;
- the merchant: the filename's merchant against the statement name, the
  statement name's distinctive words found in the receipt text, or the same
  delivery app (the statement says Uber Eats, the receipt the restaurant).

Each purchase receipt goes to one expense, strongest pairs first. A second
copy of the same receipt (itemized bill and card slip filed separately)
joins the expense its twin matched. Receipts paid from a gift card or app
balance never appear on a statement; they are linked to the latest reload of
that card at the same store.

Matches are stored in ``expense_documents`` as suggestions; confirming or
rejecting them is the owner's call (approval first). Receipts already
linked by filename in ``expenses.link`` are recorded as confirmed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import Engine, text

from .naming import slugify
from .receipt_facts import ReceiptFacts, name_tokens, platform_of, read_receipt

DATE_WINDOW = (-1, 5)  # days from the receipt date to the statement date
LONG_WINDOW = (-30, 35)  # bills, reservations, mailed orders: needs store + exact amount
RELOAD_LOOKBACK_DAYS = 400
MIN_SCORE = 5.0
CONFIRM_SCORE = 6.0  # sampled precision 87/88 at or above this score (Oct 2026)
CROWDED_AMOUNTS = 40
_MAX_TIP = 0.35

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS expense_documents (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         expense_id INTEGER NOT NULL REFERENCES expenses(id),
         document_id INTEGER NOT NULL REFERENCES scanned_documents(id),
         score REAL NOT NULL DEFAULT 0,
         reasons TEXT,
         method TEXT NOT NULL,            -- auto / twin / stored_value / link / manual / receipt
         status TEXT NOT NULL,            -- suggested / confirmed / rejected
         created_at INTEGER DEFAULT (unixepoch()),
         UNIQUE (expense_id, document_id)
       )""",
    "CREATE INDEX IF NOT EXISTS ix_expense_documents_expense ON expense_documents (expense_id)",
    "CREATE INDEX IF NOT EXISTS ix_expense_documents_document ON expense_documents (document_id)",
    # Receipts whose own expense the owner deleted (the app records them): not made again.
    """CREATE TABLE IF NOT EXISTS dismissed_receipts (
         document_id INTEGER PRIMARY KEY,
         reason TEXT,
         dismissed_at INTEGER DEFAULT (unixepoch())
       )""",
]


def ensure_schema(engine: Engine) -> None:
    from .text_index import ensure_schema as ensure_text_schema

    ensure_text_schema(engine)
    with engine.begin() as connection:
        for statement in _SCHEMA:
            connection.exec_driver_sql(statement)


def amounts_in(text_: str) -> set[str]:
    return read_receipt(text_).amounts


# ------------------------------------------------------------------ scoring


def merchant_score(expense: dict, doc: dict) -> tuple[float, str]:
    """2 = names agree, 1 = partly, 0 = no evidence; with the reason."""
    from rapidfuzz import fuzz

    names = [expense.get("merchant"), expense.get("original_input_name")]
    best = 0.0
    if doc.get("merchant"):
        receipt_name = doc["merchant"].replace("_", " ")
        for name in names:
            if name:
                best = max(best, fuzz.token_set_ratio(receipt_name, slugify(name).replace("_", " ")))
    if best >= 80:
        return 2.0, "merchant names agree"
    facts: ReceiptFacts = doc["facts"]
    found = [t for t in name_tokens(*names) if t in facts.squashed]
    if any(len(t) >= 5 for t in found):
        return 2.0, f"statement name '{found[0]}' is on the receipt"
    platform = platform_of(" ".join(n for n in names if n))
    if platform and platform == facts.platform:
        return 2.0, f"ordered through {platform.replace('_', ' ')}"
    if best >= 65 or found:
        return 1.0, "merchant names partly agree"
    return 0.0, ""


@dataclass
class Candidate:
    expense_id: int
    document_id: int
    score: float
    reasons: list[str]
    method: str = "auto"
    # False: only a round amount printed somewhere on the receipt (an item price,
    # "$10.00") ties them, with no store agreement; never confirmed automatically.
    sure: bool = True


def score_pair(expense: dict, doc: dict, card_map: Optional[dict[str, str]] = None) -> Optional[Candidate]:
    facts: ReceiptFacts = doc["facts"]
    days = (date.fromisoformat(expense["date"]) - doc["doc_date"]).days
    if not LONG_WINDOW[0] <= days <= LONG_WINDOW[1]:
        return None
    value = abs(expense["amount"])
    amount = f"{value:.2f}"
    printed_total = facts.charged if facts.charged is not None else doc.get("ocr_total")
    reasons, score = [], 0.0
    exact = amount in facts.amounts
    if exact:
        score += 3
        reasons.append(f"${amount} printed on the receipt")
        if printed_total is not None and f"{printed_total:.2f}" == amount:
            score += 1
            reasons.append("it is the amount paid")
    elif not (printed_total and 0 <= days <= 3 and printed_total < value <= printed_total * (1 + _MAX_TIP)):
        return None  # an amount match is required
    merchant, merchant_reason = merchant_score(expense, doc)
    if len(facts.amounts) > CROWDED_AMOUNTS and merchant < 2:
        return None  # a statement or long invoice: any amount appears somewhere on it

    card_points = 0.0
    if card_map:
        receipt_cards = {card_map[l4] for l4 in facts.last4 if l4 in card_map}
        if receipt_cards:
            if expense.get("card") not in receipt_cards:
                return None  # paid with a different card
            card_points = 1.5
            reasons.append(f"same card ({expense['card']})")

    if not exact:
        if not merchant:
            return None
        score += 2
        reasons.append(f"receipt total ${printed_total:.2f} plus a {value / printed_total - 1:.0%} tip")

    if DATE_WINDOW[0] <= days <= DATE_WINDOW[1]:
        score += 2 if days == 0 else 1 if days <= 3 else 0.5
        reasons.append("same day" if days == 0 else f"posted {days} day(s) after" if days > 0 else "posted a day before")
    else:
        # Long gaps need the exact amount and either the store or the card to agree.
        if not exact or not (merchant == 2 or card_points):
            return None
        reasons.append(f"posted {days} days after" if days > 0 else f"charged {-days} days before (booked ahead)")

    score += merchant + card_points
    if merchant_reason:
        reasons.append(merchant_reason)
    paid_line = printed_total is not None and f"{printed_total:.2f}" == amount
    sure = bool(merchant) or paid_line or not amount.endswith(".00")
    if not sure:
        reasons.append("only a round amount ties them (check)")
    return Candidate(expense["id"], doc["id"], score, reasons, sure=sure) if score >= MIN_SCORE else None


# ------------------------------------------------------------------ loading


def _load(engine: Engine):
    with engine.connect() as connection:
        expenses = [dict(r._mapping) for r in connection.execute(text(
            "SELECT id, date, amount, merchant, original_input_name, link, card, spender FROM expenses "
            "WHERE amount > 0 AND coalesce(is_duplicate, 0) = 0"
        ))]
        docs = []
        rows = connection.execute(text(
            "SELECT d.id, d.path, d.kind, d.person, d.doc_date, d.merchant, d.ocr_total, "
            "group_concat(p.text, char(10)) AS body "
            "FROM scanned_documents d JOIN scanned_pages p ON p.document_id = d.id "
            "WHERE d.doc_date IS NOT NULL GROUP BY d.id"
        ))
        for r in rows:
            m = r._mapping
            doc_date = m["doc_date"] if isinstance(m["doc_date"], date) else date.fromisoformat(str(m["doc_date"])[:10])
            docs.append({
                "id": m["id"], "path": m["path"], "kind": m["kind"], "person": m["person"], "doc_date": doc_date,
                "merchant": m["merchant"], "ocr_total": float(m["ocr_total"]) if m["ocr_total"] is not None else None,
                "facts": read_receipt(m["body"] or ""),
            })
    return expenses, docs


def _index_by_date(expenses: list[dict]) -> dict[date, list[dict]]:
    by_date: dict[date, list[dict]] = defaultdict(list)
    for expense in expenses:
        by_date[date.fromisoformat(expense["date"])].append(expense)
    return by_date


def _candidates_for(doc: dict, by_date: dict[date, list[dict]], card_map, skip_expenses=frozenset()) -> list[Candidate]:
    found = []
    for offset in range(LONG_WINDOW[0], LONG_WINDOW[1] + 1):
        for expense in by_date.get(doc["doc_date"] + timedelta(days=offset), ()):
            if expense["id"] in skip_expenses:
                continue
            candidate = score_pair(expense, doc, card_map)
            if candidate:
                found.append(candidate)
    return found


def _assign(candidates: list[Candidate]) -> list[Candidate]:
    used_expenses, used_docs, chosen = set(), set(), []
    for c in sorted(candidates, key=lambda c: (-c.score, c.document_id)):
        if c.expense_id in used_expenses or c.document_id in used_docs:
            continue
        used_expenses.add(c.expense_id)
        used_docs.add(c.document_id)
        chosen.append(c)
    return chosen


def learn_card_map(pairs: list[tuple[set[str], str]], min_count: int = 2, min_share: float = 0.9) -> dict[str, str]:
    """Last four digits -> statement card, from receipts matched with confidence."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for last4s, card in pairs:
        for l4 in last4s:
            counts[l4][card] += 1
    card_map = {}
    for l4, cards in counts.items():
        card, n = cards.most_common(1)[0]
        if n >= min_count and n / sum(cards.values()) >= min_share:
            card_map[l4] = card
    return card_map


def _purchase_docs(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d["facts"].tender != "gift" and d["facts"].amounts]


def _twins(docs: list[dict], chosen: list[Candidate]) -> list[Candidate]:
    """Unmatched second copies of a matched receipt join the same expense."""
    by_id = {d["id"]: d for d in docs}
    matched = {c.document_id: c for c in chosen}
    keyed: dict[tuple, int] = {}
    for doc_id in matched:
        doc = by_id[doc_id]
        paid = doc["facts"].charged or doc["ocr_total"]
        if paid:
            keyed[(doc["doc_date"], f"{paid:.2f}")] = doc_id
    extra = []
    for doc in docs:
        if doc["id"] in matched:
            continue
        paid = doc["facts"].charged or doc["ocr_total"]
        twin = keyed.get((doc["doc_date"], f"{paid:.2f}")) if paid else None
        if twin is not None:
            source = matched[twin]
            extra.append(Candidate(source.expense_id, doc["id"], source.score,
                                   [f"same purchase as {by_id[twin]['path'].rsplit('/', 1)[-1]}"], "twin",
                                   sure=source.sure))
    return extra


def _same_store(expense: dict, doc: dict) -> bool:
    """Filename merchant and statement merchant name the same store (no text search)."""
    from rapidfuzz import fuzz

    receipt_name = (doc.get("merchant") or "").replace("_", " ")
    return bool(receipt_name) and any(
        name and fuzz.token_set_ratio(receipt_name, slugify(name).replace("_", " ")) >= 85
        for name in (expense.get("merchant"), expense.get("original_input_name"))
    )


def _stored_value(docs: list[dict], expenses: list[dict]) -> list[Candidate]:
    """Gift card / app balance receipts -> the latest reload at the same store.

    Reloads are whole-dollar charges ($10, $25) at the same store before the purchase.
    """
    links = []
    reload_charges = [e for e in expenses if float(abs(e["amount"])).is_integer()]
    for doc in docs:
        if doc["facts"].tender != "gift" or not doc.get("merchant"):
            continue
        reloads = []
        for expense in reload_charges:
            gap = (doc["doc_date"] - date.fromisoformat(expense["date"])).days
            if 0 <= gap <= RELOAD_LOOKBACK_DAYS and _same_store(expense, doc):
                reloads.append((gap, expense))
        if reloads:
            gap, reload = min(reloads, key=lambda r: r[0])
            links.append(Candidate(reload["id"], doc["id"], 5.5, [
                f"paid from a gift card or app balance; latest reload at this store was "
                f"{reload['date']} (${abs(reload['amount']):.2f})"], "stored_value"))
    return links


# ------------------------------------------------------------------ matching


def plan_matches(expenses: list[dict], docs: list[dict], confirmed: dict[int, int],
                 decided: set[tuple[int, int]]) -> tuple[list[Candidate], dict[str, str]]:
    """All suggested links. ``confirmed`` maps document -> expense already decided."""
    by_date = _index_by_date(expenses)
    open_docs = [d for d in _purchase_docs(docs) if d["id"] not in confirmed]
    taken_expenses = set(confirmed.values())

    # Pass 1 without card knowledge, to learn which card is which.
    first = _assign([c for d in open_docs for c in _candidates_for(d, by_date, None, taken_expenses)])
    by_doc = {d["id"]: d for d in docs}
    by_expense = {e["id"]: e for e in expenses}
    pairs = [(by_doc[d]["facts"].last4, by_expense[e]["card"]) for d, e in confirmed.items()
             if d in by_doc and e in by_expense]
    pairs += [(by_doc[c.document_id]["facts"].last4, by_expense[c.expense_id]["card"]) for c in first if c.score >= 7]
    card_map = learn_card_map([(l4s, card) for l4s, card in pairs if l4s and card])

    # Pass 2 with the card map.
    candidates = [c for d in open_docs for c in _candidates_for(d, by_date, card_map, taken_expenses)]
    candidates = [c for c in candidates if (c.expense_id, c.document_id) not in decided]
    chosen = _assign(candidates)
    chosen += _twins([d for d in docs if d["id"] not in confirmed], chosen)
    linked = {c.document_id for c in chosen} | set(confirmed)
    chosen += [c for c in _stored_value([d for d in docs if d["id"] not in linked], expenses)
               if (c.expense_id, c.document_id) not in decided]
    return chosen, card_map


@dataclass
class MatchReport:
    linked_by_filename: int = 0
    suggested: int = 0
    confirmed: int = 0
    kept_decisions: int = 0
    replaced_receipt_expenses: int = 0
    by_method: Counter = field(default_factory=Counter)
    card_map: dict = field(default_factory=dict)


def _fill_link(connection, expense_id: int, path: str) -> None:
    """Confirmed receipts also fill an empty expenses.link, as hand-linked receipts do."""
    connection.exec_driver_sql(
        "UPDATE expenses SET link = ? WHERE id = ? AND (link IS NULL OR link = '')",
        (path.rsplit("/", 1)[-1], expense_id),
    )


def _merge_receipt_expense(connection, receipt_expense: int, statement_expense: int) -> str:
    """The statement row takes over from the expense made from its receipt.

    The statement is the record of what was charged (amount with tip, posting
    date, card), so its row stays. What the owner decided on the receipt
    expense carries over: an approved receipt expense passes its category,
    tag, split and friend shares on and the statement row counts as approved;
    an unapproved one passes its category on only where the statement row's
    own category was a guess still waiting for validation.
    """
    columns = "type, subtype, category_id, subtype_id, tag, split, is_approved, amount"
    receipt = connection.exec_driver_sql(f"SELECT {columns} FROM expenses WHERE id = ?", (receipt_expense,)).one()
    statement = connection.exec_driver_sql(
        "SELECT needs_validation, is_approved, amount FROM expenses WHERE id = ?", (statement_expense,)).one()
    if receipt.is_approved:
        connection.exec_driver_sql(
            "UPDATE expenses SET type = ?, subtype = ?, category_id = ?, subtype_id = ?, "
            "tag = coalesce(?, tag), split = coalesce(?, split), is_approved = 1, needs_validation = 0, "
            "approved_at = coalesce(approved_at, unixepoch()) WHERE id = ?",
            (receipt.type, receipt.subtype, receipt.category_id, receipt.subtype_id, receipt.tag, receipt.split,
             statement_expense),
        )
        # Who-paid-what columns the app keeps (scaled to the amount actually charged, e.g. with a tip).
        present = {r[1] for r in connection.exec_driver_sql("PRAGMA table_info(expenses)")}
        carried = [c for c in ("member_a_spend", "member_b_spend", "log_to_work", "track_with_friends") if c in present]
        if carried:
            scale = statement.amount / receipt.amount if receipt.amount else 1.0
            values = connection.exec_driver_sql(
                f"SELECT {', '.join(carried)} FROM expenses WHERE id = ?", (receipt_expense,)).one()
            sets = [(c, round(v * scale, 2) if c.endswith("_spend") and v is not None else v)
                    for c, v in zip(carried, values)]
            connection.exec_driver_sql(
                f"UPDATE expenses SET {', '.join(f'{c} = ?' for c, _ in sets)} WHERE id = ?",
                (*[v for _, v in sets], statement_expense))
        connection.exec_driver_sql(
            "UPDATE validation_queue SET status = 'validated', validated_at = CURRENT_TIMESTAMP "
            "WHERE expense_id = ? AND status = 'pending'", (statement_expense,))
        tables = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "expense_friend_shares" in tables:
            connection.exec_driver_sql("UPDATE expense_friend_shares SET expense_id = ? WHERE expense_id = ?",
                                       (statement_expense, receipt_expense))
        note = "approved receipt expense merged in (its category and approval kept)"
    elif statement.needs_validation and not statement.is_approved and receipt.type not in (None, "Uncategorized"):
        connection.exec_driver_sql(
            "UPDATE expenses SET type = ?, subtype = ?, category_id = ?, subtype_id = ? WHERE id = ?",
            (receipt.type, receipt.subtype, receipt.category_id, receipt.subtype_id, statement_expense),
        )
        connection.exec_driver_sql(
            "UPDATE validation_queue SET suggested_category_id = ?, suggested_subtype_id = ? "
            "WHERE expense_id = ? AND status = 'pending'",
            (receipt.category_id, receipt.subtype_id, statement_expense))
        note = "receipt expense merged in (category from the receipt)"
    else:
        note = "receipt expense merged in"
    _remove_receipt_expense(connection, receipt_expense)
    return note


def _remove_receipt_expense(connection, expense_id: int) -> None:
    connection.exec_driver_sql("DELETE FROM expense_documents WHERE expense_id = ?", (expense_id,))
    connection.exec_driver_sql("DELETE FROM validation_queue WHERE expense_id = ?", (expense_id,))
    tables = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "expense_friend_shares" in tables:
        connection.exec_driver_sql("DELETE FROM expense_friend_shares WHERE expense_id = ?", (expense_id,))
    connection.exec_driver_sql("DELETE FROM expenses WHERE id = ?", (expense_id,))


def match_receipts(engine: Engine, confirm_above: Optional[float] = None) -> MatchReport:
    """Rebuild suggestions; keep confirmed/rejected decisions and manual links.

    ``confirm_above``: links of at least this score are confirmed directly
    (see CONFIRM_SCORE for the measured precision). Expenses created from a
    receipt are set aside: when a statement row matches that receipt strongly,
    the receipt expense is merged into the statement row (_merge_receipt_expense).
    """
    from .receipt_expenses import receipt_source_id

    ensure_schema(engine)
    expenses, docs = _load(engine)
    report = MatchReport()
    by_name = {d["path"].rsplit("/", 1)[1].lower(): d for d in docs}
    by_doc = {d["id"]: d for d in docs}
    source_id = receipt_source_id(engine)

    with engine.begin() as connection:
        receipt_expenses = {
            r[0]: bool(r[1]) for r in connection.exec_driver_sql(
                "SELECT id, is_approved FROM expenses WHERE source_file_id = ?", (source_id,)
            )
        } if source_id else {}
        expenses = [e for e in expenses if e["id"] not in receipt_expenses]
        rows = connection.exec_driver_sql(
            "SELECT expense_id, document_id, status FROM expense_documents WHERE status != 'suggested'"
        ).all()
        decided = {(r[0], r[1]) for r in rows if r[0] not in receipt_expenses}
        confirmed = {r[1]: r[0] for r in rows if r[2] == "confirmed" and r[0] not in receipt_expenses}
        from_receipt = {r[1]: r[0] for r in rows if r[0] in receipt_expenses}
        # A suggestion confirmed by hand since the last run: merge now.
        for doc_id, receipt_expense in list(from_receipt.items()):
            if doc_id in confirmed:
                _merge_receipt_expense(connection, receipt_expense, confirmed[doc_id])
                report.replaced_receipt_expenses += 1
                del from_receipt[doc_id]
        report.kept_decisions = len(decided)
        connection.exec_driver_sql("DELETE FROM expense_documents WHERE status = 'suggested'")

        for expense in expenses:
            link = (expense["link"] or "").strip()
            doc = by_name.get(link.rsplit("/", 1)[-1].lower()) if link and not link.startswith("http") else None
            if doc and (expense["id"], doc["id"]) not in decided:
                connection.exec_driver_sql(
                    "INSERT OR IGNORE INTO expense_documents (expense_id, document_id, score, reasons, method, status) "
                    "VALUES (?, ?, 10, 'linked by filename in the expense', 'link', 'confirmed')",
                    (expense["id"], doc["id"]),
                )
                report.linked_by_filename += 1
                confirmed[doc["id"]] = expense["id"]
                decided.add((expense["id"], doc["id"]))

        chosen, report.card_map = plan_matches(expenses, docs, confirmed, decided)
        for c in chosen:
            strong = (confirm_above is not None and c.method in ("auto", "twin") and c.score >= confirm_above
                      and c.sure)
            reasons = "; ".join(c.reasons)
            receipt_expense = from_receipt.get(c.document_id)
            if receipt_expense is not None:
                if strong:
                    reasons += "; " + _merge_receipt_expense(connection, receipt_expense, c.expense_id)
                    report.replaced_receipt_expenses += 1
                else:
                    # Not sure enough to merge: the receipt expense stays until this is confirmed.
                    reasons += "; an expense was also made from this receipt (confirming merges them)"
            status = "confirmed" if strong else "suggested"
            if strong:
                reasons += f"; confirmed automatically (score {c.score:g} >= {confirm_above:g})"
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO expense_documents (expense_id, document_id, score, reasons, method, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (c.expense_id, c.document_id, round(c.score, 2), reasons, c.method, status),
            )
            if strong:
                _fill_link(connection, c.expense_id, by_doc[c.document_id]["path"])
                report.confirmed += 1
            else:
                report.suggested += 1
            report.by_method[c.method] += 1
    return report


def evaluate_against_links(engine: Engine) -> dict:
    """How the matcher does on expenses the owner linked by filename (held out: links ignored)."""
    expenses, docs = _load(engine)
    by_name = {d["path"].rsplit("/", 1)[1].lower(): d for d in docs}
    # Only the owner's links: not ones the matcher filled in when it confirmed
    # a match, and not expenses made from a receipt (they link their own file).
    with engine.connect() as connection:
        ensure_schema(engine)
        machine = {(r[0], r[1]) for r in connection.execute(text(
            "SELECT expense_id, document_id FROM expense_documents "
            "WHERE method NOT IN ('link', 'manual')"))}
        from_receipts = {r[0] for r in connection.execute(text(
            "SELECT id FROM expenses WHERE content_hash LIKE 'receipt:%'"))}
    truth = {}
    for e in expenses:
        link = (e["link"] or "").strip()
        if link and not link.startswith("http") and link.rsplit("/", 1)[-1].lower() in by_name:
            doc_id = by_name[link.rsplit("/", 1)[-1].lower()]["id"]
            if e["id"] not in from_receipts and (e["id"], doc_id) not in machine:
                truth[e["id"]] = doc_id
    chosen, _ = plan_matches(expenses, docs, {}, set())
    predicted: dict[int, set[int]] = defaultdict(set)
    for c in chosen:
        predicted[c.expense_id].add(c.document_id)
    correct = wrong = none = 0
    misses = []
    for e in expenses:
        if e["id"] not in truth:
            continue
        got = predicted.get(e["id"])
        if not got:
            none += 1
            misses.append(f"no match: {e['date']} {e['merchant']} ${e['amount']}")
        elif truth[e["id"]] in got:
            correct += 1
        else:
            wrong += 1
            misses.append(f"wrong match: {e['date']} {e['merchant']} ${e['amount']}")
    return {"linked": len(truth), "correct": correct, "wrong": wrong, "no_match": none, "misses": misses,
            "evaluated_at": datetime.now().isoformat(timespec="seconds")}
