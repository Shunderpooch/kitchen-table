from datetime import date, datetime, timezone

import pytest
from backend.src.db import init_db
from backend.src.db.session import get_engine
from backend.src.fetch import importer, simplefin
from sqlalchemy import text


def epoch(day):
    return int(datetime(*day, 12, tzinfo=timezone.utc).timestamp())


def response(transactions, errlist=(), account_id="ACT-1", name="Rewards Visa Signature"):
    return simplefin.Response(simplefin.Window(date(2026, 9, 1), date(2026, 10, 1)), {
        "errlist": list(errlist),
        "connections": [{"conn_id": "CON-1", "name": "Sample Bank", "org_id": "o", "sfin_url": "x"}],
        "accounts": [{"id": account_id, "name": name, "conn_id": "CON-1", "currency": "USD",
                      "balance": "-321.40", "balance-date": epoch((2026, 9, 30)), "transactions": transactions}],
    })


def txn(txn_id, day, amount, description, **extra):
    return {"id": txn_id, "posted": epoch(day), "amount": amount, "description": description, **extra}


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "e.db"
    init_db(path)
    return get_engine(path)


def expenses(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT date, amount, merchant, card, spender, content_hash FROM expenses "
                              "ORDER BY date, id")).all()


def test_transactions_become_expenses_once(engine, tmp_path):
    batch = [
        txn("T1", (2026, 9, 3), "-4.50", "CORNER CAFE"),
        txn("T2", (2026, 9, 3), "-4.50", "CORNER CAFE"),           # a second coffee: a second expense
        txn("T3", (2026, 9, 10), "250.00", "PAYMENT THANK YOU"),    # paying the card: not an expense
        txn("T4", (2026, 9, 12), "19.99", "OUTDOOR STORE REFUND"),  # refund: a negative expense
        txn("T5", (2026, 9, 14), "-60.00", "GROCERY MART", transacted_at=epoch((2026, 9, 13))),
        txn("T6", (2026, 9, 20), "-8.00", "PENDING THING", pending=True),
    ]
    report = importer.import_responses(engine, [response(batch)], now=datetime(2026, 10, 1, 7, 13),
                                       snapshot_dir=tmp_path / "snap")
    assert (report.new, report.expenses, report.skipped) == (5, 4, {"payment": 1})
    rows = expenses(engine)
    assert [(str(r.date), r.amount, r.merchant) for r in rows] == [
        ("2026-09-03", 4.5, "CORNER CAFE"), ("2026-09-03", 4.5, "CORNER CAFE"),
        ("2026-09-12", -19.99, "OUTDOOR STORE REFUND"), ("2026-09-13", 60.0, "GROCERY MART")]
    assert rows[0].card == "Sample Bank Rewards Visa Signature"
    assert report.snapshot.exists() and "CORNER CAFE" in report.snapshot.read_text()
    # Re-fetching the same window adds nothing.
    again = importer.import_responses(engine, [response(batch)], now=datetime(2026, 10, 2, 7, 13))
    assert (again.new, again.expenses) == (0, 0) and len(expenses(engine)) == 4
    account = importer.accounts(engine)[0]
    assert account["last_transaction_date"] == "2026-09-14" and account["kind"] == "card"


def test_a_statement_row_already_recorded_is_linked_not_duplicated(engine):
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO expenses (date, type, merchant, original_input_name, amount, split, card, "
                          "is_duplicate, needs_validation, is_approved, content_hash, created_at) VALUES "
                          "('2026-09-05', 'Groceries', 'grocery_mart', 'GROCERY MART #12', 60.0, 1.0, 'Alex Visa', "
                          "0, 0, 1, 'stmt-1', CURRENT_TIMESTAMP)")
    report = importer.import_responses(engine, [response([txn("T9", (2026, 9, 6), "-60.00", "GROCERY MART 12")])],
                                       now=datetime(2026, 10, 1))
    assert (report.expenses, report.linked) == (0, 1)
    with engine.connect() as c:
        assert c.execute(text("SELECT outcome, expense_id FROM fetched_transactions")).one() == ("linked", 1)


def test_bank_deposits_are_not_expenses_and_errors_are_kept(engine):
    error = {"code": "con.auth", "msg": "Reconnect required", "account_id": "ACT-2"}
    report = importer.import_responses(engine, [response(
        [txn("D1", (2026, 9, 1), "2500.00", "EMPLOYER DIRECT DEP"), txn("D2", (2026, 9, 2), "-1200.00", "RENT")],
        errlist=[error, {"code": "gen.warn", "msg": "Too many requests"}], account_id="ACT-2", name="Checking")],
        now=datetime(2026, 10, 1))
    assert report.skipped == {"deposit": 1} and report.expenses == 1
    assert report.errors == ["gen.warn: Too many requests"]
    assert importer.accounts(engine)[0]["last_error"] == "con.auth: Reconnect required"


def test_renaming_an_account_relabels_its_expenses(engine, monkeypatch):
    importer.import_responses(engine, [response([txn("T1", (2026, 9, 3), "-4.50", "CORNER CAFE")])],
                              now=datetime(2026, 10, 1))
    assert importer.set_account(engine, "ACT-1", card="Sam Visa", spender="Sam") == 1
    assert expenses(engine)[0].card == "Sam Visa" and expenses(engine)[0].spender == "Sam"
    with pytest.raises(ValueError):
        importer.set_account(engine, "nope", card="x")


def test_request_windows_stay_under_the_bridge_limit():
    parts = simplefin.windows(date(2026, 1, 1), date(2026, 7, 1))
    assert parts[0].start == date(2026, 1, 1) and parts[-1].end == date(2026, 7, 1)
    assert all((w.end - w.start).days <= 45 for w in parts) and len(parts) == 5


def test_fetch_window_overlaps_the_last_transaction(engine):
    importer.import_responses(engine, [response([txn("T1", (2026, 9, 14), "-4.50", "CAFE")])], now=datetime(2026, 10, 1))
    seen = {}

    def fake(access_url, start, end):
        seen["window"] = (start, end)
        return []

    importer.run_fetch(engine, "https://u:p@example.org/simplefin", today=date(2026, 10, 1), fetcher=fake)
    assert seen["window"] == (date(2026, 9, 9), date(2026, 10, 2))


def test_setup_tokens_are_checked():
    with pytest.raises(simplefin.SimplefinError):
        simplefin.claim("not a token")
