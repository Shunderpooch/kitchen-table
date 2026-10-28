from datetime import date, time

from backend.src.scans.extract import (
    extract_fields,
    find_date,
    find_date_and_time,
    find_time,
    find_total,
    match_known_merchant,
)
from backend.src.scans.naming import build_filename, parse_filename, slugify, unique_path

from .conftest import NEW_RESTAURANT, TRADER_JOES

TODAY = date(2026, 10, 18)


def test_slugify_matches_onedrive_style():
    assert slugify("TRADER JOE'S") == "trader_joes"
    assert slugify("C'est-La Vie") == "cest-la_vie"
    assert slugify("Barnes & Noble") == "barnes_and_noble"
    assert slugify("  Café Descartes!! ") == "cafe_descartes"
    assert slugify("The Golden Spoon Bistro Downtown", max_words=4) == "the_golden_spoon_bistro"


def test_parse_and_build_round_trip():
    for name in [
        "2026-09-27_19-55_trader_joes.pdf",
        "2026-08-28_copley_garage.pdf",
        "2026-08-29_17-06.pdf",
        "2026-03_civic_service_request.pdf",
    ]:
        parsed = parse_filename(name)
        assert build_filename(parsed.date, parsed.time, parsed.slug) == name


def test_parse_legacy_variants():
    assert parse_filename("2022-07-01-15-32_iced_house_mocha.pdf").time == "15-32"
    assert parse_filename("2022-10-21_13-50_belles_bread (2).pdf").slug == "belles_bread"
    assert parse_filename("dewalt_dcs334.pdf").date is None


def test_unique_path_never_overwrites(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    assert unique_path(tmp_path, "a.pdf").name == "a_2.pdf"
    assert unique_path(tmp_path, "a.pdf", taken=[tmp_path / "a_2.pdf"]).name == "a_3.pdf"


def test_date_time_total_parsing():
    assert find_date("Date: 9/26/26 visit", today=TODAY) == date(2026, 9, 26)
    assert find_date("Oct 3, 2026", today=TODAY) == date(2026, 10, 3)
    assert find_date("3 October 2026", today=TODAY) == date(2026, 10, 3)
    # Future dates (return-by, expiry) are not transaction dates.
    assert find_date("Return by 12/31/2026", today=TODAY) is None
    assert find_time("7:42 PM") == time(19, 42)
    assert find_time("12:05 am") == time(0, 5)
    assert find_time("at 14:05:33") == time(14, 5)
    assert find_total("SUBTOTAL 35.00\nTAX 2.19\nTOTAL 37.19") == "37.19"
    assert find_total("Total Savings 3.00\nGrand Total $1,204.50") == "1204.50"


def test_dates_in_glued_ocr_text():
    # Shapes taken from real FastFoto + OCR output.
    assert find_date_and_time("Receipt#930986\nDate09/27/25Time09:16\nPump#05", today=TODAY) == (
        date(2025, 9, 27), time(9, 16)
    )
    assert find_date_and_time("Total: $187.45\n2034661212/16/202308:10PM\n", today=TODAY) == (
        date(2023, 12, 16), time(20, 10)
    )
    assert find_date_and_time("VISA 43.00\n05/26/2108:22pm330500211999999500", today=TODAY) == (
        date(2021, 5, 26), time(20, 22)
    )
    assert find_date("Kenny Center\nNov 12,2022 06:23:54 PM", today=TODAY) == date(2022, 11, 12)
    assert find_date("Purchase Date/Time:02:21pm Sep01,2025", today=TODAY) == date(2025, 9, 1)
    # A price like "1@4/5.00" is not a date; the labelled date wins.
    assert find_date("STO BLACK BEANS\n1@4/5.00\nDate:07/03/23\nTime:07:18PM", today=TODAY) == date(2023, 7, 3)


def test_extract_known_merchant_from_history():
    fields = extract_fields(TRADER_JOES, ["trader_joes", "joes", "cvs_pharmacy"], today=TODAY)
    assert fields.merchant == "trader_joes"
    assert fields.merchant_source == "history"
    assert fields.date == date(2026, 9, 26)
    assert fields.time == time(14, 5)
    assert fields.total == "41.17"


def test_extract_new_merchant_from_header():
    fields = extract_fields(NEW_RESTAURANT, ["trader_joes"], today=TODAY)
    assert fields.merchant == "the_golden_spoon_bistro"
    assert fields.merchant_source == "header"
    assert fields.time == time(19, 42)


def test_fuzzy_merchant_tolerates_ocr_noise():
    fields = extract_fields("TRADER J0ES\nTOTAL 3.00\n09/26/2026", ["trader_joes"], today=TODAY)
    assert fields.merchant == "trader_joes"


def test_common_receipt_words_never_win_as_merchant():
    known = ["sale", "toast", "connies_restaurant", "dunkin", "cumberland_farms"]
    assert match_known_merchant("CONNIE S RESTAURANT\nCredit Sale:\nToast", known) == "connies_restaurant"
    assert match_known_merchant("Nelcone to Dunkin\nSALE", known) == "dunkin"
    assert match_known_merchant("CUMBERLANDFARMS\nSTORE 4764", known) == "cumberland_farms"
    assert match_known_merchant("SALE\nThank you", known) is None


def test_empty_text_is_explained():
    fields = extract_fields("", ["trader_joes"])
    assert fields.merchant is None
    assert "no text" in fields.reasons[0]


def test_meridiem_glued_to_time_zone():
    from datetime import time

    from backend.src.scans.extract import find_time

    assert find_time("Posted Date: 11/11/21 at 02:12 PMEST") == time(14, 12)
    assert find_time("10:30 Amount 5.00") == time(10, 30)


def test_document_names_and_short_lines_are_not_stores():
    known = {"harbor_market_receipt": 1, "payment_receipt": 3, "boarding_pass": 2, "united": 4, "coffee": 2}
    # "RECEIPT" is inside a past filename, not the other way round.
    assert match_known_merchant("RECEIPT\nWelcome To\nNew Hampshire State Parks", known) is None
    assert match_known_merchant("City General Hospital\nPayment Receipt", known) is None
    assert match_known_merchant("UNITED STATES\nPOSTAL SERVICE", known) is None
    assert match_known_merchant("Moscone Center\n1 M cOFfEE", known) is None
    assert match_known_merchant("Harbor Market Receipt\nSushi 12.00", known) == "harbor_market_receipt"


def test_header_guess_skips_addresses_amounts_and_dates():
    from backend.src.scans.extract import guess_merchant_from_header

    assert guess_merchant_from_header("88 Oak St.\nRiverton, MA 01103\nCasa Azul") == "casa_azul"
    assert guess_merchant_from_header("Total $9.30\nTuesday, December 2, 2025\nAndytown") == "andytown"


def test_date_glued_to_the_time():
    from backend.src.scans.extract import find_date_and_time

    day, at = find_date_and_time("SHAWS\nTOTAL 54.10\n9/28/2416:58\nThank you", today=date(2026, 10, 18))
    assert day == date(2024, 9, 28) and at == time(16, 58)


def test_a_tied_date_or_a_store_filed_in_several_folders_needs_a_look(layout):
    from backend.src.scans.classify import decide
    from backend.src.scans.extract import extract_fields

    known = layout.known_merchants()
    tied = "TRADER JOE'S\nDate: 09/01/2026 10:15\nDate: 09/05/2026\nTOTAL 12.00\n"
    extracted = extract_fields(tied, known, today=date(2026, 10, 18))
    assert extracted.date_rivals == 1
    assert decide(layout, extracted, tied, 1).needs_review
    # CVS is filed under grocery once and health_care twice.
    cvs = "CVS pharmacy\n09/02/2026 14:20\nSUBTOTAL 8.00\nTAX 0.49\nTOTAL 8.49\nVISA 8.49\nTHANK YOU\n"
    decision = decide(layout, extract_fields(cvs, known, today=date(2026, 10, 18)), cvs, 1)
    assert decision.needs_review and "several folders" in "; ".join(decision.reasons)
