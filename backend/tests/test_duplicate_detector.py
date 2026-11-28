from datetime import date
from decimal import Decimal

from backend.src.db import Expense, init_db, session_scope
from backend.src.validate.duplicate_detector import find_all_duplicates_in_batch


def test_a_receipt_made_expense_does_not_hide_its_statement_row(tmp_path):
    db = tmp_path / "e.db"
    init_db(db)
    with session_scope(db) as session:
        session.add(Expense(date=date(2026, 9, 18), type="Gas", merchant="Corner Fuel", amount=Decimal("76.23"),
                            split=1.0, content_hash="receipt:749"))
        session.add(Expense(date=date(2026, 9, 20), type="Coffee", merchant="Bean Bar", amount=Decimal("4.50"),
                            split=1.0, content_hash="abc"))
        session.flush()
        statement = Expense(date=date(2026, 9, 18), type="CORNER FUEL 0197", merchant="Corner Fuel",
                            amount=Decimal("76.23"), split=1.0, card="Alex Robinhood", content_hash="row1")
        repeat = Expense(date=date(2026, 9, 20), type="BEAN BAR", merchant="Bean Bar", amount=Decimal("4.50"),
                         split=1.0, card="Alex Robinhood", content_hash="row2")
        results = find_all_duplicates_in_batch([statement, repeat], session)
        assert not statement.is_duplicate  # receipt matching merges the receipt expense into it
        assert repeat.is_duplicate and results["exact_duplicates"] == 1
