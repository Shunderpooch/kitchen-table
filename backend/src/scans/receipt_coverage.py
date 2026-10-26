"""Where every filed receipt stands: linked to an expense, or why not."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import Engine, text

from .receipt_expenses import plan_receipt_expenses, receipt_source_id

UNMATCHED = "expense made from the receipt, but its statement is in and no row matched (check for a duplicate)"
ORDER = [
    "linked to a statement expense",
    "linked to the gift-card reload that paid it",
    UNMATCHED,
    "expense made from the receipt (awaiting approval)",
    "expense made from the receipt (approved)",
    "suggested link (review)",
    "would become an expense (run receipts-to-expenses)",
]


def coverage(engine: Engine) -> dict:
    plan = plan_receipt_expenses(engine)
    would_create = {doc_id for p in plan.created for doc_id in p.document_ids}
    with engine.connect() as connection:
        docs = connection.execute(text(
            "SELECT id, kind FROM scanned_documents WHERE kind IN ('receipt', 'car')")).all()
        links = connection.execute(text(
            "SELECT l.document_id, l.method, l.status, e.is_approved FROM expense_documents l "
            "JOIN expenses e ON e.id = l.expense_id WHERE l.status != 'rejected'")).all()
        unmatched = {r[0] for r in connection.execute(text(
            "WITH statements AS (SELECT card, min(date) AS lo, max(date) AS hi FROM expenses "
            "  WHERE coalesce(source_file_id, -1) != :s AND card IS NOT NULL GROUP BY card), "
            "span AS (SELECT min(lo) AS lo, max(hi) AS hi FROM statements) "
            "SELECT l.document_id FROM expense_documents l JOIN expenses e ON e.id = l.expense_id "
            "LEFT JOIN statements c ON c.card = e.card, span "
            "WHERE l.method = 'receipt' AND coalesce(e.card, '') != 'Cash' "
            "AND e.date BETWEEN coalesce(c.lo, span.lo) AND coalesce(c.hi, span.hi)"),
            {"s": receipt_source_id(engine) or -1})}
    best: dict[int, str] = {}
    rank = {status: i for i, status in enumerate(ORDER)}
    for doc_id, method, status, approved in links:
        if status == "confirmed" and method == "receipt" and doc_id in unmatched:
            label = UNMATCHED
        elif status == "confirmed":
            label = ("expense made from the receipt (approved)" if approved else
                     "expense made from the receipt (awaiting approval)") if method == "receipt" else (
                "linked to the gift-card reload that paid it" if method == "stored_value" else
                "linked to a statement expense")
        else:
            label = "suggested link (review)"
        if doc_id not in best or rank[label] < rank[best[doc_id]]:
            best[doc_id] = label
    counts: Counter = Counter()
    by_kind: dict[str, Counter] = {"receipt": Counter(), "car": Counter()}
    for doc_id, kind in docs:
        label = best.get(doc_id) or ("would become an expense (run receipts-to-expenses)" if doc_id in would_create
                                     else plan.reason_by_doc.get(doc_id, "no date in the filename"))
        counts[label] += 1
        by_kind[kind][label] += 1
    ordered = {k: counts[k] for k in ORDER if counts[k]}
    ordered.update({k: n for k, n in counts.most_common() if k not in ordered})
    return {"total": len(docs), "by_status": ordered,
            "by_kind": {kind: dict(c.most_common()) for kind, c in by_kind.items()}}
