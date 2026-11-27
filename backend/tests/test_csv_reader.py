from backend.src.ingest.csv_reader import infer_statement_card_name, read_expenses_csv, summarize_input_file

ROBINHOOD = (
    "Date,Time,Cardholder,Amount,Points,Balance,Status,Type,Merchant,Description\n"
    '2026-11-19,"5:54 PM","Alex Doe",57.86,174,955.46,Posted,Purchase,"Corner Pie","TST* CORNER PIE 555-010-7090 MA"\n'
    '2026-11-16,"3:00 PM","Alex Doe",22.16,0,897.60,Posted,Interest,"Interest Charge","INTEREST CHARGE ON PURCHASES"\n'
    '2026-11-09,"7:33 AM","Alex Doe",-300.00,0,875.44,Posted,Payment,Payment,"PAYMENT - THANK YOU"\n'
    '2026-10-18,"1:00 PM","Alex Doe",-9.30,0,1175.44,Posted,Refund,"Refund: Shop","SHOP MARK* 5R48 SEATTLE WA"\n'
    '2026-09-07,"9:00 PM","Alex Doe",19.11,0,1184.74,Declined,Purchase,"Game Store",\n'
)


def test_robinhood_export(tmp_path):
    path = tmp_path / "9066daad.csv"
    path.write_text(ROBINHOOD, encoding="utf-8")
    rows = list(read_expenses_csv(path, card_name="Alex Robinhood"))
    assert [(str(e.date), float(e.amount)) for e in rows] == [
        ("2026-11-19", 57.86), ("2026-11-16", 22.16), ("2026-10-18", -9.30)]  # payment and declined skipped
    first = rows[0]
    assert (first.merchant, first.original_input_name, first.card) == (
        "Corner Pie", "TST* CORNER PIE 555-010-7090 MA", "Alex Robinhood")
    assert summarize_input_file(path, card_name="Alex Robinhood") == {"payments_or_credits_skipped": 1}


def test_robinhood_card_name_from_filename(tmp_path):
    assert infer_statement_card_name(tmp_path / "robinhood_activity.csv", spender_name="Alex") == "Alex Robinhood"
