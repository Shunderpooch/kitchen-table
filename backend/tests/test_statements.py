from datetime import date
from decimal import Decimal

import pytest
from click.testing import CliRunner
from sqlalchemy import text

from backend.src import main
from backend.src.db import init_db
from backend.src.db.session import get_engine
from backend.src.statements import archive


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "e.db"
    init_db(path)
    return path


def add_expense(engine, day, amount, merchant, card="Alex Amex", content_hash=None):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO expenses (date, type, merchant, original_input_name, amount, split, card, is_duplicate, "
            "needs_validation, is_approved, content_hash, created_at) VALUES (:d, :m, :m, :m, :a, 1.0, :c, 0, 0, 0, "
            ":h, CURRENT_TIMESTAMP)"), {"d": day, "m": merchant, "a": amount, "c": card, "h": content_hash})
        return c.execute(text("SELECT last_insert_rowid()")).scalar()


def fetch_card(engine, card="Alex Amex"):
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE IF NOT EXISTS fetch_accounts (provider TEXT, account_id TEXT, card TEXT)")
        c.execute(text("INSERT INTO fetch_accounts VALUES ('simplefin', 'A1', :c)"), {"c": card})


def test_rows_match_recorded_expenses_one_to_one(db):
    engine = get_engine(db)
    first = add_expense(engine, "2026-09-03", 4.50, "CORNER CAFE", content_hash="fetch:1")
    second = add_expense(engine, "2026-09-03", 4.50, "CORNER CAFE", content_hash="fetch:2")
    add_expense(engine, "2026-09-05", 60.00, "GROCERY MART", card="Sam Visa")  # another card: not this row
    rows = [archive.Row(date(2026, 9, 4), "CORNER CAFE #12", Decimal("4.50")),
            archive.Row(date(2026, 9, 4), "CORNER CAFE #12", Decimal("4.50")),
            archive.Row(date(2026, 9, 6), "GROCERY MART", Decimal("60.00"))]
    matches = archive.reconcile_rows(engine, "Alex Amex", rows)
    assert {m.expense_id for m in matches[:2]} == {first, second}
    assert matches[2].expense_id is None


def test_statement_archive_and_reconciliation(db, tmp_path):
    engine = get_engine(db)
    fetch_card(engine)
    cafe = add_expense(engine, "2026-09-03", 4.50, "CORNER CAFE", content_hash="fetch:1")
    add_expense(engine, "2026-09-20", 12.00, "BOOK SHOP", content_hash="fetch:2")  # fetched, not on statement
    statement = tmp_path / "2026-09-28_amex.csv"
    statement.write_text("Date,Description,Amount\n09/03/2026,CORNER CAFE,4.50\n09/10/2026,HARDWARE STORE,31.20\n")
    rows = [archive.Row(date(2026, 9, 3), "CORNER CAFE", Decimal("4.50")),
            archive.Row(date(2026, 9, 10), "HARDWARE STORE", Decimal("31.20"))]
    result = archive.archive_statement(engine, statement, "Alex Amex", rows, tmp_path / "archive",
                                      closing=date(2026, 9, 28))
    assert not result.already_archived and result.archived_path.exists()
    assert [m.expense_id for m in result.matches] == [cafe, None]
    again = archive.archive_statement(engine, statement, "Alex Amex", rows, tmp_path / "archive")
    assert again.already_archived and again.statement_id == result.statement_id
    report = archive.statement_report(engine, result.statement_id)
    assert report["reconciled"] is False
    assert [r["description"] for r in report["missing_from_fetch"]] == ["HARDWARE STORE"]
    assert [r["merchant"] for r in report["missing_from_statement"]] == ["BOOK SHOP"]
    grid = archive.coverage(engine, months=3, today=date(2026, 10, 15))
    assert grid["cards"]["Alex Amex"]["2026-09"] == "gaps"


def test_importing_a_statement_creates_only_unrecorded_rows(db, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATEMENT_ARCHIVE", tmp_path / "archive")
    engine = get_engine(db)
    recorded = add_expense(engine, "2026-09-03", 4.50, "CORNER CAFE", card="Amex", content_hash="fetch:1")
    statement = tmp_path / "2026-09-28_amex.csv"
    statement.write_text("Date,Description,Amount\n09/03/2026,CORNER CAFE,4.50\n09/10/2026,HARDWARE STORE,31.20\n")
    runner = CliRunner()
    args = ["--db", str(db), "process", str(statement), "--card", "Amex", "--skip-categorization"]
    first = runner.invoke(main.cli, args)
    assert first.exit_code == 0, first.output
    assert "1 rows already recorded, 1 new" in first.output
    with engine.connect() as c:
        merchants = [r[0] for r in c.execute(text("SELECT merchant FROM expenses ORDER BY id"))]
        links = c.execute(text("SELECT status, expense_id FROM statement_rows ORDER BY id")).all()
    assert merchants == ["CORNER CAFE", "HARDWARE STORE"]
    assert links[0] == ("matched", recorded) and links[1][0] == "created" and links[1][1] is not None
    second = runner.invoke(main.cli, args)
    assert second.exit_code == 0 and "already imported" in second.output
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM expenses")).scalar() == 2
