from backend.src.scans.classify import decide, find_car
from backend.src.scans.extract import extract_fields
from backend.src.scans.store_directory import CarBook, PhoneBook, car_of_path

DQ = "88 Oak St.\nRiverton, MA 01103\n(555) 014-2142\n8/14/2024 5:14:50 PM\nBlizzard 6.79\nTotal 6.79\n"


def test_a_store_is_recognised_by_its_phone_number():
    book = PhoneBook()
    for _ in range(2):
        book.add("dairy_queen", DQ)
    extracted = extract_fields(DQ, phone_book=book)
    assert extracted.merchant == "dairy_queen" and extracted.merchant_source == "phone"
    assert "phone number (2 filed receipts)" in "; ".join(extracted.reasons)
    # One earlier receipt is not enough; nor is a number shared by two stores.
    assert book.lookup(DQ, leave_out="dairy_queen") is None
    book.add("big_y", DQ)
    book.add("big_y", DQ)
    assert book.lookup(DQ) is None


def test_a_car_is_recognised_by_identifiers_on_its_paperwork(layout):
    invoice = "Rays Auto\nVIN 1G1ZT51816F109149\nPlate 7XKL22\nOil change 49.95\n"
    book = CarBook()
    book.add("2009_toyota_rav4", invoice)
    book.add("2009_toyota_rav4", "Registration renewal 1G1ZT51816F109149")
    layout.car_book = book
    car, reason = find_car(layout, "Inspection sticker\nVIN 1G1ZT51816F109149\n")
    assert car == "2009_toyota_rav4" and "identifiers" in reason
    decision = decide(layout, extract_fields(invoice), invoice, 1)
    assert decision.car == "2009_toyota_rav4"
    assert find_car(layout, "Inspection sticker\nVIN 1G1ZT51816F109149\n", leave_out="2009_toyota_rav4")[0] is None


def test_car_of_path():
    assert car_of_path("Documents/cars/2012_honda_civic_si/2024-01-02_oil.pdf") == "2012_honda_civic_si"
    assert car_of_path("Documents/cars/loose.pdf") is None
    assert car_of_path("Documents/receipts/gas/2024-01-02_shell.pdf") is None
