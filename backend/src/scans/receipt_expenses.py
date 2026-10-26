"""Turn receipts with no statement row into staged expenses.

Statements only cover part of history, so many filed receipts have no
expense to link to. Each such purchase receipt becomes an expense:

- date: the receipt date; amount: what the receipt says was paid (the
  largest amount when no "paid" line is read); spender: the household
  member whose people folder it is, otherwise member A (the owner); receipts
  for other people are tagged with them;
- card: from the card's last four digits (learned from matched receipts),
  Cash for cash receipts;
- category: what this merchant was categorized as before, else the usual
  category for the receipt's folder (restaurants by time of day).

They follow the app's approval rule: staged (not approved), in the
validation queue with reason ``from_receipt`` and a note saying where the
amount and category came from. They are linked to their receipt
(``expense_documents`` method ``receipt``) and grouped under one source file,
so they can be listed or removed together.

Receipts are left alone when a statement row should exist: inside the
statement period, paid with a card whose statements are loaded for that
date. Those are matching work, not new expenses. Receipts newer than the
statements do get an expense right away; when the statement arrives,
``match_receipts`` merges it into the statement row.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import Engine, text

from ..household import member_a, member_b, member_for
from .receipt_matching import CROWDED_AMOUNTS, _load, ensure_schema, learn_card_map

SOURCE_NAME = "scanned receipts (OneDrive)"
NOT_PURCHASE_FOLDERS = ("credit_card_payments", "credit_card_statements", "banking")
MEAL_FOLDERS = {"restaurants", "cafe", "fast_food"}
# Paperwork about money that is not itself a payment: loan contracts, offers,
# estimates, insurance summaries, statements.
NOT_PURCHASE_NAME_RE = re.compile(
    r"contract|offer|disclosure|estimate|quote|agreement|summary|application|overview|statement|"
    r"schedule|policy|declarations?|warranty|buyback|appraisal|payoff|welcome|notice|letter|"
    r"check_for|deposit|transfer|refund",
    re.I,
)
# Bills and medical statements list many charges; without a labeled amount
# paid, guessing the largest number is usually wrong.
BILL_FOLDERS = {"health_care", "services", "housing", "medical_records"}
LARGE_AMOUNT = 2000.0
# Used only when filing history says nothing about a folder: (category, subcategory).
FOLDER_DEFAULTS = {
    "gas": ("Travel", "Gasoline"),
    "grocery": ("Food and Drink - Grocery", "Grocery Store"),
    "restaurants": ("Food and Drink - Dining Out", None),
    "cafe": ("Food and Drink - Dining Out", "Cafe"),
    "fast_food": ("Food and Drink - Dining Out", "Fast Food"),
    "fast food": ("Food and Drink - Dining Out", "Fast Food"),
    "travel": ("Travel", None),
    "rental_vehicles": ("Travel", "Rental Car"),
    "health_care": ("Health Care", None),
    "clothing": ("Clothing", None),
    "beauty": ("Beauty", None),
    "home_improvement": ("Home Improvement", None),
    "tools": ("Home Improvement", "Tools"),
    "plants": ("Hobbies", "Plants"),
    "hobbies": ("Hobbies", None),
    "events": ("Hobbies", "Events"),
    "technology": ("Electronics", None),
    "furniture": ("Furniture", None),
    "shipping": ("Shipping", None),
    "automotive_supplies": ("Automotive", "Automotive Supplies"),
    "services": ("Services", None),
    "housing": ("Household Maintenance", None),
}


def receipt_source_id(engine: Engine, create: bool = False) -> Optional[int]:
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT id FROM source_files WHERE filename = :n AND file_type = 'receipt'"), {"n": SOURCE_NAME}
        ).first()
        if row or not create:
            return row[0] if row else None
        connection.execute(text(
            "INSERT INTO source_files (filename, file_type, row_count, processed_at, status) "
            "VALUES (:n, 'receipt', 0, CURRENT_TIMESTAMP, 'completed')"), {"n": SOURCE_NAME})
        return connection.execute(text("SELECT last_insert_rowid()")).scalar()


def folder_category(path: str) -> str:
    parts = path.lower().split("/")
    if "cars" in parts:
        return "cars"
    if "receipts" in parts:
        rest = parts[parts.index("receipts") + 1:-1]
        return rest[0] if rest else "receipts"
    return parts[-2] if len(parts) > 1 else ""


def meal_period(path: str) -> Optional[str]:
    name = path.rsplit("/", 1)[-1]
    if len(name) > 13 and name[10] == "_" and name[11:13].isdigit():
        hour = int(name[11:13])
        return "morning" if hour < 11 else "midday" if hour < 16 else "evening"
    return None


@dataclass
class Plan:
    document_ids: list[int]
    date: date
    amount: float
    amount_note: str
    merchant: str
    spender: Optional[str]
    card: Optional[str]
    tag: Optional[str]
    type: str
    subtype: Optional[str]
    category_id: Optional[int]
    subtype_id: Optional[int]
    category_note: str
    path: str
    awaiting_statement: bool = False  # newer than the statements: merges with its row when they arrive


@dataclass
class ReceiptExpenseReport:
    created: list[Plan] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)  # reason -> receipts
    examples: dict = field(default_factory=lambda: defaultdict(list))
    reason_by_doc: dict = field(default_factory=dict)


def _learn_categories(engine: Engine, docs_by_id: dict[int, dict]):
    """Merchant -> (type, subtype, ids) and folder[/period] -> same, from approved expenses and links."""
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT merchant, type, subtype, category_id, subtype_id FROM expenses "
            "WHERE is_approved = 1 AND coalesce(is_duplicate, 0) = 0 AND type IS NOT NULL"
        )).all()
        links = connection.execute(text(
            "SELECT l.document_id, e.type, e.subtype, e.category_id, e.subtype_id FROM expense_documents l "
            "JOIN expenses e ON e.id = l.expense_id WHERE (l.status = 'confirmed' OR l.score >= 7) "
            "AND e.is_approved = 1"
        )).all()
    by_merchant: dict[str, Counter] = defaultdict(Counter)
    for merchant, *category in rows:
        if merchant:
            by_merchant[merchant.lower()][tuple(category)] += 1
    by_folder: dict[str, Counter] = defaultdict(Counter)
    for doc_id, *category in links:
        doc = docs_by_id.get(doc_id)
        if doc:
            folder = folder_category(doc["path"])
            by_folder[folder][tuple(category)] += 1
            period = meal_period(doc["path"])
            if folder in MEAL_FOLDERS and period:
                by_folder[f"{folder}/{period}"][tuple(category)] += 1
    return by_merchant, by_folder


def _pick(counter: Counter, min_count: int = 2, min_share: float = 0.5, subtype_share: float = 0.4):
    """Majority category, then its subcategory only if one clearly leads within it."""
    if not counter:
        return None
    total = sum(counter.values())
    by_type: Counter = Counter()
    for (type_, _sub, category_id, _sub_id), n in counter.items():
        by_type[(type_, category_id)] += n
    (type_, category_id), n = by_type.most_common(1)[0]
    if n < min_count or n / total < min_share:
        return None
    within = Counter({(sub, sub_id): m for (t, sub, c, sub_id), m in counter.items() if t == type_ and c == category_id})
    (sub, sub_id), m = within.most_common(1)[0]
    if m / n < subtype_share:
        sub, sub_id = None, None
    return (type_, sub, category_id, sub_id), n, total


def _taxonomy(engine: Engine) -> dict[tuple[str, Optional[str]], tuple[int, Optional[int]]]:
    """(category name, subcategory name) -> ids, for folder defaults and car folders."""
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.name, c.id, s.name, s.id FROM categories c LEFT JOIN subtypes s ON s.category_id = c.id"
        )).all()
    names: dict = {}
    for cat_name, cat_id, sub_name, sub_id in rows:
        names[(cat_name, None)] = (cat_id, None)
        if sub_name:
            names[(cat_name, sub_name)] = (cat_id, sub_id)
    return names


def car_subtype(path: str, taxonomy: dict) -> Optional[tuple[str, str, int, int]]:
    """cars/2009_toyota_rav4/... -> Automotive / 2009 Toyota RAV4 (when that subcategory exists)."""
    from rapidfuzz import fuzz

    parts = path.split("/")
    lowered = [p.lower() for p in parts]
    if "cars" not in lowered or lowered.index("cars") + 1 >= len(parts) - 1:
        return None
    car = parts[lowered.index("cars") + 1].replace("_", " ").lower()
    best = max(((fuzz.token_set_ratio(car, sub.lower()), sub) for (cat, sub) in taxonomy
                if cat == "Automotive" and sub and sub[:2] in ("19", "20")), default=(0, None))
    if best[0] >= 85:
        cat_id, sub_id = taxonomy[("Automotive", best[1])]
        return ("Automotive", best[1], cat_id, sub_id)
    return None


def plan_receipt_expenses(engine: Engine) -> ReceiptExpenseReport:
    """Work out which receipts need an expense of their own (nothing is written)."""
    ensure_schema(engine)
    report = ReceiptExpenseReport()
    expenses, docs = _load(engine)
    source_id = receipt_source_id(engine)
    with engine.connect() as connection:
        receipt_expense_ids = {r[0] for r in connection.execute(
            text("SELECT id FROM expenses WHERE source_file_id = :s"), {"s": source_id})} if source_id else set()
        linked = {r[0] for r in connection.execute(text(
            "SELECT document_id FROM expense_documents WHERE status != 'rejected'"))}
        dismissed = {r[0] for r in connection.execute(text("SELECT document_id FROM dismissed_receipts"))}
        pairs = connection.execute(text(
            "SELECT l.document_id, e.card FROM expense_documents l JOIN expenses e ON e.id = l.expense_id "
            "WHERE (l.status = 'confirmed' OR l.score >= 7) AND l.method IN ('auto', 'link', 'manual')"
        )).all()
    statement_expenses = [e for e in expenses if e["id"] not in receipt_expense_ids]
    docs_by_id = {d["id"]: d for d in docs}
    card_map = learn_card_map([(docs_by_id[d]["facts"].last4, card) for d, card in pairs
                               if d in docs_by_id and card and docs_by_id[d]["facts"].last4])

    coverage: dict[str, tuple[str, str]] = {}
    for e in statement_expenses:
        if e["card"]:
            lo, hi = coverage.get(e["card"], (e["date"], e["date"]))
            coverage[e["card"]] = (min(lo, e["date"]), max(hi, e["date"]))
    all_lo = min((lo for lo, _ in coverage.values()), default="9999")
    all_hi = max((hi for _, hi in coverage.values()), default="0000")

    # Car folders hold decades of previous owners' paperwork; only count car
    # documents from when the owner's own receipts begin.
    own_dates = [d["doc_date"] for d in docs if d["kind"] == "receipt" and not d["person"]]
    own_receipts_start = min(own_dates) if own_dates else date.min

    by_merchant, by_folder = _learn_categories(engine, docs_by_id)
    known_merchants = list(by_merchant)
    taxonomy = _taxonomy(engine)

    def skip(reason: str, doc: dict) -> None:
        report.skipped[reason] += 1
        report.reason_by_doc[doc["id"]] = reason
        if len(report.examples[reason]) < 5:
            report.examples[reason].append(doc["path"].rsplit("/", 1)[-1])

    groups: dict[tuple, list[dict]] = {}
    for doc in docs:
        if doc["kind"] not in ("receipt", "car") or doc["id"] in linked:
            continue
        facts = doc["facts"]
        folder = folder_category(doc["path"])
        if doc["id"] in dismissed:
            skip("you deleted the expense made from this receipt", doc)
            continue
        if folder in NOT_PURCHASE_FOLDERS:
            skip("payment, statement, or banking document", doc)
            continue
        if len(facts.amounts) > CROWDED_AMOUNTS and facts.charged_label not in ("paid", "due"):
            skip("statement-like document (many amounts, no amount-due line)", doc)
            continue
        if doc["kind"] == "car" and facts.charged is None:
            skip("car paperwork without an amount paid", doc)
            continue
        if doc["kind"] == "car" and doc["doc_date"] < own_receipts_start:
            skip("car history from before your own receipts (previous owners)", doc)
            continue
        if NOT_PURCHASE_NAME_RE.search(doc["path"].rsplit("/", 1)[-1]):
            skip("contract, offer, estimate, statement, or similar (not a payment)", doc)
            continue
        if facts.tender == "gift":
            skip("paid from a gift card or app balance (the reload is the expense)", doc)
            continue
        day = doc["doc_date"].isoformat()
        cards = {card_map[l4] for l4 in facts.last4 if l4 in card_map}
        card = next(iter(cards)) if len(cards) == 1 else ("Cash" if facts.tender == "cash" else None)
        if card != "Cash":  # cash never reaches a statement
            card_lo, card_end = coverage.get(card, (all_lo, all_hi))
            # Newer than the statements: staged now, merged with its row when they arrive.
            if all_lo <= day <= card_end and day >= card_lo:
                skip("in the statement period: should match a statement row (review)", doc)
                continue
        amount = receipt_amount(doc)
        if folder in BILL_FOLDERS and facts.charged is None and not doc["ocr_total"]:
            amount = None
        if not amount or amount <= 0:
            skip("no amount readable (enter it by hand)", doc)
            continue
        key = (day, f"{amount:.2f}", (doc["merchant"] or "")[:12])
        groups.setdefault(key, []).append(doc)

    from rapidfuzz import process

    def awaiting(day: str, card: Optional[str]) -> bool:
        return card != "Cash" and day > (coverage[card][1] if card in coverage else all_hi)

    for (day, amount, _), group in groups.items():
        doc = group[0]
        person = doc["person"]
        # The owner's own receipts have no people folder; only the partner's
        # folder (first name matching member B) counts as household spending.
        partner = member_for(person) if person else None
        partner = partner if partner and partner == member_b() else None
        spender: Optional[str] = partner or member_a()
        tag = None
        if person and not partner:
            # Someone else's receipt: who paid is the owner's call at approval.
            parts = person.split("_")
            tag = f"for {' '.join(p.capitalize() for p in reversed(parts))}"
            spender = None
        facts = doc["facts"]
        cards = {card_map[l4] for l4 in facts.last4 if l4 in card_map}
        card = next(iter(cards)) if len(cards) == 1 else ("Cash" if facts.tender == "cash" else None)

        category, category_note = None, "category needs choosing"
        merchant = (doc["merchant"] or "").lower()
        if merchant and known_merchants:
            match = process.extractOne(merchant, known_merchants, score_cutoff=88)
            if match:
                picked = _pick(by_merchant[match[0]], min_count=1)
                if picked:
                    category = picked[0]
                    category_note = f"category as '{match[0]}' was categorized before ({picked[1]} of {picked[2]})"
        folder = folder_category(doc["path"])
        if folder == "cars" and category and category[0] == "Automotive":
            # Same shop, different car: the car folder names the car.
            this_car = car_subtype(doc["path"], taxonomy)
            if this_car and this_car[1] != category[1] and (category[1] or "")[:2] in ("19", "20"):
                category = this_car
                category_note += f"; car from the folder ({this_car[1]})"
        if "mortgage" in doc["path"].lower() and ("Loans", "Mortgage") in taxonomy:
            category = ("Loans", "Mortgage", *taxonomy[("Loans", "Mortgage")])
            category_note = "mortgage bill"
        if category is None and folder == "cars":
            category = car_subtype(doc["path"], taxonomy)
            if category:
                category_note = f"car folder: {category[0]} / {category[1]}"
        if category is None:
            period = meal_period(doc["path"])
            for key in ([f"{folder}/{period}"] if folder in MEAL_FOLDERS and period else []) + [folder]:
                picked = _pick(by_folder.get(key, Counter()), min_count=3)
                if picked:
                    category = picked[0]
                    category_note = f"usual category for '{key}' receipts ({picked[1]} of {picked[2]})"
                    break
        if category is None and folder in FOLDER_DEFAULTS and FOLDER_DEFAULTS[folder] in taxonomy:
            name = FOLDER_DEFAULTS[folder]
            category = (name[0], name[1], *taxonomy[name])
            category_note = f"'{folder}' folder"
        type_, subtype, category_id, subtype_id = category if category else ("Uncategorized", None, None, None)
        amount_note = _amount_note(doc)
        if float(amount) >= LARGE_AMOUNT:
            amount_note += f"; large amount (${float(amount):,.2f}): confirm you paid it"
        report.created.append(Plan(
            document_ids=[d["id"] for d in group], date=date.fromisoformat(day), amount=float(amount),
            amount_note=amount_note, merchant=doc["merchant"] or doc["path"].rsplit("/", 1)[-1],
            spender=spender, card=card, tag=tag, type=type_, subtype=subtype, category_id=category_id,
            subtype_id=subtype_id, category_note=category_note, path=doc["path"],
            awaiting_statement=awaiting(day, card),
        ))
    return report


def receipt_amount(doc: dict) -> Optional[float]:
    """Amount paid: the 'paid' line, else the total, else the largest amount printed."""
    facts = doc["facts"]
    if facts.charged is not None:
        return facts.charged
    if doc["ocr_total"]:
        return doc["ocr_total"]
    candidates = sorted(float(a) for a in facts.amounts if 0 < float(a) < 5000)
    return candidates[-1] if candidates else None


def _amount_note(doc: dict) -> str:
    facts = doc["facts"]
    if facts.charged is not None:
        note = f"amount read as the {facts.charged_label} line"
    elif doc["ocr_total"]:
        note = "amount read as the receipt total"
    else:
        note = "amount is the largest number on the receipt (check it)"
    if facts.tip is None and folder_category(doc["path"]) in MEAL_FOLDERS and doc["kind"] == "receipt":
        note += "; a tip written on the slip may be missing"
    return note


def create_receipt_expenses(engine: Engine, dry_run: bool = True) -> ReceiptExpenseReport:
    report = plan_receipt_expenses(engine)
    if dry_run or not report.created:
        return report
    source_id = receipt_source_id(engine, create=True)
    with engine.begin() as connection:
        for plan in report.created:
            filename = plan.path.rsplit("/", 1)[-1]
            connection.execute(text(
                "INSERT INTO expenses (date, type, subtype, merchant, original_input_name, amount, split, "
                "spender, card, tag, link, source_file_id, category_id, subtype_id, is_duplicate, "
                "needs_validation, is_approved, content_hash, created_at) VALUES (:date, :type, :subtype, "
                ":merchant, :original, :amount, 1.0, :spender, :card, :tag, :link, :source, :category_id, "
                ":subtype_id, 0, 1, 0, :hash, CURRENT_TIMESTAMP)"), {
                "date": plan.date.isoformat(), "type": plan.type, "subtype": plan.subtype,
                "merchant": plan.merchant, "original": f"Receipt: {filename}", "amount": round(plan.amount, 2),
                "spender": plan.spender, "card": plan.card, "tag": plan.tag, "link": filename,
                "source": source_id, "category_id": plan.category_id, "subtype_id": plan.subtype_id,
                "hash": f"receipt:{plan.document_ids[0]}",
            })
            expense_id = connection.execute(text("SELECT last_insert_rowid()")).scalar()
            connection.execute(text(
                "INSERT INTO validation_queue (expense_id, reason, suggested_category_id, suggested_subtype_id, status) "
                "VALUES (:e, 'from_receipt', :c, :s, 'pending')"),
                {"e": expense_id, "c": plan.category_id, "s": plan.subtype_id})
            for doc_id in plan.document_ids:
                connection.execute(text(
                    "INSERT OR IGNORE INTO expense_documents (expense_id, document_id, score, reasons, method, status) "
                    "VALUES (:e, :d, 10, :r, 'receipt', 'confirmed')"),
                    {"e": expense_id, "d": doc_id, "r": f"expense created from this receipt; {plan.amount_note}; "
                                                         f"{plan.category_note}" + (
                                                             "; no statement for this date yet (it merges with "
                                                             "the statement row when that is imported)"
                                                             if plan.awaiting_statement else "")})
        connection.execute(text("UPDATE source_files SET row_count = (SELECT count(*) FROM expenses "
                                "WHERE source_file_id = :s) WHERE id = :s"), {"s": source_id})
    return report
