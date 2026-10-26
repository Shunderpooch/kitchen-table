from datetime import date

from sqlalchemy import create_engine, text

from backend.src.scans.receipt_facts import read_receipt
from backend.src.scans.receipt_matching import (
    amounts_in, ensure_schema, learn_card_map, match_receipts, plan_matches, score_pair,
)


def doc(body="", **kw):
    base = {"id": 1, "path": "x/2024-06-13_20-31_uber.pdf", "kind": "receipt", "person": None,
            "doc_date": date(2024, 6, 13), "merchant": "uber", "ocr_total": None, "facts": read_receipt(body)}
    base.update(kw)
    return base


def expense(**kw):
    base = {"id": 7, "date": "2024-06-14", "amount": 19.45, "merchant": "uber", "original_input_name": "UBER *TRIP",
            "card": "Alex Amex", "link": None, "spender": "Alex"}
    base.update(kw)
    return base


def test_receipt_facts():
    facts = read_receipt("BURGER KING\nSUBTOTAL 30.22\nTOTAL 30.22\nAMEX XXXXXXXXXXX3005 30.22\n")
    assert facts.charged == 30.22 and facts.last4 == {"3005"} and facts.tender == "card"
    assert read_receipt("Starbucks Gift Card $6.37\nBalance: 3.29").tender == "gift"
    assert read_receipt("Get a $25 gift card with any purchase\nTOTAL 12.00\nVISA 12.00").tender == "card"
    assert read_receipt("Placed via Uber Eats\nAmount paid $18.19").charged == 18.19
    assert read_receipt("Uber Eats order").platform == "uber_eats"
    suggested = read_receipt("Total $73.56\nSuggested Tip: 20%: (Tip $13.56 Total $87.12)")
    assert suggested.charged == 73.56
    assert amounts_in("Total $1,204.50 tip 3.00 qty 2") == {"1204.50", "3.00"}


def test_score_needs_the_amount_and_a_close_date():
    assert score_pair(expense(), doc("Total $19.45")).score >= 5
    assert score_pair(expense(), doc("Total $19.60")) is None
    assert score_pair(expense(), doc("Total $19.30", merchant="lyft")) is None  # a tip needs the same store
    assert score_pair(expense(date="2024-06-25"), doc("Total $19.45", merchant="lyft")) is None


def test_tip_on_the_card_slip():
    c = score_pair(expense(amount=41.49, merchant="farmerbrown", date="2024-06-16"),
                   doc("Total $35.66", merchant="farmerbrown", doc_date=date(2024, 6, 16), ocr_total=35.66))
    assert c and "tip" in "; ".join(c.reasons)


def test_long_gap_needs_store_and_exact_amount():
    bill = doc("National Grid\nAmount due $557.74", merchant="national_grid", doc_date=date(2023, 10, 9))
    assert score_pair(expense(amount=557.74, merchant="national_grid", date="2023-10-24"), bill)
    assert score_pair(expense(amount=557.74, merchant="verizon", date="2023-10-24"), bill) is None


def test_statement_wording_on_the_receipt_and_delivery_apps():
    c = score_pair(expense(amount=23.36, merchant="d_m_auto_parts", original_input_name="D & M AUTO PARTS"),
                   doc("D& M AUTO PARTS,INC.\nTOTAI DUE 23.36\nAMEX 23.36", merchant="dandm_auto"))
    assert c and "on the receipt" in "; ".join(c.reasons)
    c = score_pair(expense(amount=18.19, merchant="uber_eats", original_input_name="UBER *EATS"),
                   doc("Uber Eats\nMr Charlie's\nAmount paid $18.19", merchant="mr_charlies"))
    assert c and "uber eats" in "; ".join(c.reasons)


def test_a_different_card_rules_a_match_out():
    receipt = doc("Total $19.45\nVISA ************4417", merchant="uber")
    card_map = {"4417": "Sam Discover"}
    assert score_pair(expense(card="Alex Amex"), receipt, card_map) is None
    assert "same card" in "; ".join(score_pair(expense(card="Sam Discover"), receipt, card_map).reasons)


def test_a_round_item_price_alone_is_never_sure():
    # A $10.00 dish on a restaurant bill, the same day and card as a $10 transit reload.
    bill = doc("Noodle House\nGyoza 10.00\nRamen 22.00\nTotal 57.88\nVISA ************4417", merchant="noodle_house")
    card_map = {"4417": "Sam Discover"}
    c = score_pair(expense(amount=10.0, merchant="transit_card", card="Sam Discover", date="2024-06-13"), bill, card_map)
    assert c and c.score >= 6 and not c.sure
    assert score_pair(expense(amount=57.88, merchant="noodle_house", card="Sam Discover"), bill, card_map).sure


def test_crowded_statement_needs_the_store():
    statement = doc(" ".join(f"{i}.99" for i in range(1, 60)), merchant="rfcu_statement")
    assert score_pair(expense(amount=2.99, merchant="youtube"), statement) is None


def test_learn_card_map_needs_agreement():
    pairs = [({"3005"}, "Alex Amex")] * 3 + [({"8880"}, "Alex Amex"), ({"8880"}, "Sam Discover")]
    assert learn_card_map(pairs) == {"3005": "Alex Amex"}


def test_twins_and_gift_card_reloads():
    expenses = [expense(id=1, amount=121.31, merchant="trattoria_romana", date="2026-02-01"),
                expense(id=2, amount=10.0, merchant="starbucks", date="2024-02-19")]
    docs = [doc("Trattoria\nTOTAL 121.31", id=10, merchant="trattoria_romana", doc_date=date(2026, 2, 1)),
            doc("TIP 121.31 TOTAL", id=11, merchant="trattoria_romana_bar", doc_date=date(2026, 2, 1), ocr_total=121.31),
            doc("Venti 5.95\nStarbucks Gift Card $6.37", id=12, merchant="starbucks", doc_date=date(2024, 2, 28))]
    chosen, _ = plan_matches(expenses, docs, {}, set())
    by_doc = {c.document_id: (c.expense_id, c.method) for c in chosen}
    assert by_doc[10][0] == 1 and by_doc[11][0] == 1
    assert by_doc[12] == (2, "stored_value")


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE expenses (id INTEGER PRIMARY KEY, date TEXT, type TEXT, subtype TEXT, "
                          "amount REAL, merchant TEXT, original_input_name TEXT, link TEXT, card TEXT, spender TEXT, "
                          "split REAL, tag TEXT, source_file_id INTEGER, category_id INTEGER, subtype_id INTEGER, "
                          "is_duplicate INTEGER DEFAULT 0, needs_validation INTEGER DEFAULT 0, "
                          "is_approved INTEGER DEFAULT 1, approved_at INTEGER, content_hash TEXT, created_at TEXT)")
        c.exec_driver_sql("CREATE TABLE source_files (id INTEGER PRIMARY KEY, filename TEXT, file_type TEXT, "
                          "row_count INTEGER, processed_at TEXT, status TEXT, error_message TEXT)")
        c.exec_driver_sql("CREATE TABLE validation_queue (id INTEGER PRIMARY KEY, expense_id INTEGER, reason TEXT, "
                          "suggested_category_id INTEGER, suggested_subtype_id INTEGER, status TEXT, validated_at TEXT)")
        c.exec_driver_sql("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT)")
        c.exec_driver_sql("CREATE TABLE subtypes (id INTEGER PRIMARY KEY, category_id INTEGER, name TEXT)")
    ensure_schema(engine)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO expenses (id, date, type, amount, merchant, original_input_name, link, card) "
                          "VALUES (1, '2024-06-14', 'Travel', 19.45, 'uber', 'UBER', NULL, 'Alex Amex'), "
                          "(2, '2024-06-14', 'Travel', 19.30, 'uber', 'UBER', '2024-06-13_21-01_uber.pdf', 'Alex Amex'), "
                          "(3, '2024-06-14', 'Food', 5.00, 'dunkin', 'DUNKIN', NULL, 'Alex Amex')")
    return engine


def add_doc(engine, doc_id, path, body, merchant, day):
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO scanned_documents (id, path, folder, kind, merchant, doc_date, page_count, "
                          "indexed_at) VALUES (?, ?, 'r', 'receipt', ?, ?, 1, CURRENT_TIMESTAMP)",
                          (doc_id, path, merchant, day))
        c.exec_driver_sql("INSERT INTO scanned_pages (document_id, page_number, text, source) VALUES (?, 1, ?, 'ocr')",
                          (doc_id, body))


def test_match_receipts_keeps_decisions_and_confirms_strong_links(tmp_path):
    engine = make_db(tmp_path)
    add_doc(engine, 1, "Documents/receipts/travel/2024-06-13_20-31_uber.pdf", "Uber\nTotal $19.45", "uber", "2024-06-13")
    add_doc(engine, 2, "Documents/receipts/travel/2024-06-13_21-01_uber.pdf", "Uber\nTotal $19.30", "uber", "2024-06-13")
    report = match_receipts(engine, confirm_above=6)
    assert (report.linked_by_filename, report.confirmed) == (1, 1)
    with engine.begin() as c:
        rows = c.execute(text("SELECT expense_id, document_id, method, status FROM expense_documents "
                              "ORDER BY expense_id")).all()
        assert [tuple(r) for r in rows] == [(1, 1, "auto", "confirmed"), (2, 2, "link", "confirmed")]
        assert c.execute(text("SELECT link FROM expenses WHERE id = 1")).scalar() == "2024-06-13_20-31_uber.pdf"
        c.exec_driver_sql("UPDATE expense_documents SET status = 'rejected' WHERE expense_id = 1")
    match_receipts(engine)  # a rejected pair is not suggested again
    with engine.begin() as c:
        assert c.execute(text("SELECT status FROM expense_documents WHERE expense_id = 1")).scalar() == "rejected"


def test_receipt_expenses_and_their_replacement_by_a_statement_row(tmp_path):
    from backend.src.scans.receipt_expenses import create_receipt_expenses

    engine = make_db(tmp_path)
    old = "Documents/receipts/grocery/2021-03-02_18-10_market_basket.pdf"
    add_doc(engine, 5, old, "Market Basket\nTOTAL 42.10\nVISA 42.10", "market_basket", "2021-03-02")
    add_doc(engine, 6, "Documents/receipts/banking/2021-03-02_credit_union_deposit.pdf", "Deposit $500.00", "credit_union", "2021-03-02")
    report = create_receipt_expenses(engine, dry_run=False)
    assert [(p.amount, p.document_ids) for p in report.created] == [(42.10, [5])]
    assert "payment, statement, or banking document" in report.skipped
    with engine.begin() as c:
        row = c.execute(text("SELECT id, is_approved, needs_validation, link FROM expenses WHERE content_hash = "
                             "'receipt:5'")).one()
        assert (row[1], row[2], row[3]) == (0, 1, "2021-03-02_18-10_market_basket.pdf")
        assert c.execute(text("SELECT reason FROM validation_queue WHERE expense_id = :e"), {"e": row[0]}).scalar() \
            == "from_receipt"
        # Later the statement for that month is imported:
        c.exec_driver_sql("INSERT INTO expenses (id, date, type, amount, merchant, original_input_name, card) "
                          "VALUES (50, '2021-03-03', 'Food', 42.10, 'market_basket', 'MARKET BASKET', 'Alex Amex')")
    match_receipts(engine)  # only a suggestion: the receipt expense stays
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM expenses WHERE content_hash = 'receipt:5'")).scalar() == 1
    report = match_receipts(engine, confirm_above=6)
    assert report.replaced_receipt_expenses == 1
    with engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM expenses WHERE content_hash = 'receipt:5'")).scalar() == 0
        assert c.execute(text("SELECT expense_id FROM expense_documents WHERE document_id = 5")).scalar() == 50


def test_an_approved_receipt_expense_keeps_its_category_when_the_statement_arrives(tmp_path):
    from backend.src.scans.receipt_expenses import create_receipt_expenses

    engine = make_db(tmp_path)
    # The statements so far end in June 2024; this receipt is newer, so it is staged now.
    add_doc(engine, 5, "Documents/receipts/grocery/2024-07-02_18-10_market_basket.pdf",
            "Market Basket\nTOTAL 42.10\nVISA 42.10", "market_basket", "2024-07-02")
    report = create_receipt_expenses(engine, dry_run=False)
    assert [p.awaiting_statement for p in report.created] == [True]
    with engine.begin() as c:
        c.exec_driver_sql("UPDATE expenses SET type = 'Groceries', tag = 'party', is_approved = 1, "
                          "needs_validation = 0 WHERE content_hash = 'receipt:5'")
        c.exec_driver_sql("INSERT INTO expenses (id, date, type, amount, merchant, original_input_name, card, "
                          "needs_validation, is_approved) VALUES (60, '2024-07-03', 'Uncategorized', 42.10, "
                          "'market_basket', 'MARKET BASKET', 'Alex Amex', 1, 0)")
        c.exec_driver_sql("INSERT INTO validation_queue (expense_id, reason, status) VALUES (60, 'low', 'pending')")
    match_receipts(engine, confirm_above=6)
    with engine.begin() as c:
        row = c.execute(text("SELECT type, tag, is_approved, needs_validation, amount FROM expenses "
                             "WHERE id = 60")).one()
        assert tuple(row) == ("Groceries", "party", 1, 0, 42.10)
        assert c.execute(text("SELECT count(*) FROM expenses WHERE content_hash = 'receipt:5'")).scalar() == 0
        assert c.execute(text("SELECT status FROM validation_queue WHERE expense_id = 60")).scalar() == "validated"


def test_a_dismissed_receipt_is_not_made_into_an_expense_again(tmp_path):
    from backend.src.scans.receipt_expenses import create_receipt_expenses

    engine = make_db(tmp_path)
    add_doc(engine, 5, "Documents/receipts/grocery/2021-03-02_18-10_market_basket.pdf",
            "Market Basket\nTOTAL 42.10", "market_basket", "2021-03-02")
    assert len(create_receipt_expenses(engine, dry_run=False).created) == 1
    with engine.begin() as c:  # what the app does when the expense is deleted
        c.exec_driver_sql("INSERT INTO dismissed_receipts (document_id, reason) VALUES (5, 'deleted')")
        c.exec_driver_sql("DELETE FROM expense_documents")
        c.exec_driver_sql("DELETE FROM expenses WHERE content_hash = 'receipt:5'")
    report = create_receipt_expenses(engine, dry_run=False)
    assert report.created == [] and "you deleted the expense made from this receipt" in report.skipped
