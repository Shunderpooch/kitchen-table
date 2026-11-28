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


def test_robinhood_foreign_purchase_uses_merchant(tmp_path):
    path = tmp_path / "9066daad.csv"
    path.write_text(
        "Date,Time,Cardholder,Amount,Points,Balance,Status,Type,Merchant,Description\n"
        '2026-11-07,"7:49 AM","Alex Doe",13.11,39,95.64,Posted,Purchase,"Harbor Bakery","FOREIGN TRANSACTION FEE"\n',
        encoding="utf-8")
    (row,) = read_expenses_csv(path, card_name="Alex Robinhood")
    assert (row.merchant, row.original_input_name, float(row.amount)) == ("Harbor Bakery", "Harbor Bakery", 13.11)


BOFA_PAGE = """Bank of America
Statement Closing Date 09/05/2026
Payments and Other Credits
09/02    09/02    PMT FROM BILL PAYER SERVICE          1234   5678    -1,238.00
09/02    09/02    ONLINE/MOBILE PAYMENT CONF#abc123    1234   5678      -312.00
Purchases and Adjustments
08/19    08/20    MOUNTAIN OUTFITTERS                  1234   5678       656.00
Balance Transfers
08/24    08/24    BALANCE TRANSFER AMERICAN EXP        1234   5678     6,300.00
Fees
08/24    08/24    BALANCE TRANSFER - TRANSACTION FEE   1234   5678       252.00
"""


def test_bofa_balance_transfers_and_bill_payer_payments(tmp_path, monkeypatch):
    from backend.src.ingest import csv_reader

    class Page:
        def extract_text(self, **_):
            return BOFA_PAGE

    monkeypatch.setattr(csv_reader, "PdfReader", lambda _: type("R", (), {"pages": [Page()]})())
    rows = csv_reader.parse_bofa_pdf_rows(tmp_path / "eStmt_2026-09-05.pdf", spender_name="Alex")
    assert [(r["Type"], r["Amount"]) for r in rows] == [
        ("ONLINE/MOBILE PAYMENT CONF#abc123", "-312.00"),  # skipped later, as every "PAYMENT" row is
        ("MOUNTAIN OUTFITTERS", "656.00"),
        ("BALANCE TRANSFER - TRANSACTION FEE", "252.00"),
    ]


def test_identical_rows_in_one_statement_are_separate_charges(tmp_path):
    path = tmp_path / "Discover-2026-YearToDateSummary.csv"
    path.write_text(
        "Trans. Date,Post Date,Description,Amount,Category\n"
        '04/02/2026,04/02/2026,"PP*SKYAIR 888-555-0100 CA",392.80,"Travel/ Entertainment"\n'
        '04/02/2026,04/02/2026,"PP*SKYAIR 888-555-0100 CA",392.80,"Travel/ Entertainment"\n', encoding="utf-8")
    first, second = read_expenses_csv(path, card_name="Alex Discover")
    assert first.content_hash != second.content_hash
    assert [e.content_hash for e in read_expenses_csv(path, card_name="Alex Discover")] == [
        first.content_hash, second.content_hash]  # the same file reads the same way again


def test_robinhood_card_name_from_filename(tmp_path):
    assert infer_statement_card_name(tmp_path / "robinhood_activity.csv", spender_name="Alex") == "Alex Robinhood"
