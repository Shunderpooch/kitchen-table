from datetime import date

from PIL import Image
from pypdf import PdfReader

from backend.src.scans.grouping import page_marker
from backend.src.scans.pipeline import apply_manifest, plan_batch, write_manifest

from .conftest import DEWALT_MANUAL, TRADER_JOES, scan_image, scan_with_text

TODAY = date(2026, 10, 18)

DUNKIN_MORNING = "DUNKIN'\nMedium Latte 4.29\nTOTAL 4.29\nVISA\n10/01/2026 08:12\n"
DUNKIN_AFTERNOON = "DUNKIN'\nIced Coffee 3.19\nTOTAL 3.19\nVISA\n10/01/2026 15:40\n"
TJ_CARD_SLIP = "Card slip\nVISA ************1234\nAMOUNT 41.17\nAPPROVED\n09/26/2026 14:05\nSignature ________\n"
STATEMENT_1 = "Springfield Water Department\nPage 1 of 2\nAccount 4471\nQuarterly usage 10/02/2026\n"
STATEMENT_2 = "Springfield Water Department\nPage 2 of 2\nUsage history\nTotal due 88.40\n"
MANUAL_PAGE = "3 Operation\nAttach the blade before use. Keep hands clear.\n"


def stack(tmp_path, pages):
    """FastFoto-style images with sidecar text; None means a page with no text."""
    inbox = tmp_path / "inbox"
    for number, text in enumerate(pages, start=1):
        path = inbox / f"FastFoto_{number:04d}.jpg"
        scan_image(path) if text is None else scan_with_text(path, text)
    return inbox


def documents(rows):
    """item_id groups, e.g. [["0001", "0002"], ["0003"]]."""
    result = []
    for row in rows:
        if row["merge_into"]:
            result[-1].append(row["item_id"])
        else:
            result.append([row["item_id"]])
    return result


def test_page_marker():
    assert page_marker("ACME\nPage 2 of 3\n") == 2
    assert page_marker("page 1/4") == 1
    assert page_marker("Pager 3 of 4") is None
    assert page_marker("details on page 3") is None


def test_pages_join_into_whole_documents(tmp_path, layout):
    inbox = stack(tmp_path, [
        TRADER_JOES,
        "Thank you for shopping\nReturns within 30 days",  # printed back
        TJ_CARD_SLIP,                                        # same date and time
        DUNKIN_MORNING,
        DUNKIN_AFTERNOON,                                    # same merchant, later: separate
        DEWALT_MANUAL,
        MANUAL_PAGE,
        "4 Maintenance\nWarranty and service",
        STATEMENT_1,
        STATEMENT_2,
        None,                                                # unreadable: joined for review
    ])
    rows = plan_batch(inbox, layout, today=TODAY)
    assert documents(rows) == [
        ["0001", "0002", "0003"],
        ["0004"],
        ["0005"],
        ["0006", "0007", "0008"],
        ["0009", "0010", "0011"],
    ]
    by_id = {r["item_id"]: r for r in rows}
    assert by_id["0009"]["action"] == "review"  # the unreadable page needs a look
    assert by_id["0001"]["filename"] == "2026-09-26_14-05_trader_joes.pdf"
    assert "same date and time" in by_id["0001"]["reasons"]
    assert by_id["0006"]["filename"] == "dewalt_dcs356.pdf"
    assert "says page 2" in by_id["0009"]["reasons"]


REPAIR_1 = """Main Street Auto
9 Mill St Riverton MA 01103
(413) 555-0142
Customer: Alex Smith   Vehicle: 2009 Toyota RAV4
Drop-off 04/02/2026
Brake pads front 189.00
"""
REPAIR_2 = """Main Street Auto  (413) 555-0142
Labor 3.0 hrs 405.00
Shop supplies 22.00
TOTAL 616.00
Completed 04/09/2026 15:20
"""
RESTAURANT_BILL = """Marigold
40 Pine St Springfield MA 01105
Server: Dana  Table 4
Entree 24.00
Total 41.50
10/03/2026 7:42 PM
"""
RESTAURANT_SLIP = """Marigold
40 Pine St Springfield MA 01105
VISA ****1234
Amount 41.50
Tip 8.00
Total 49.50
10/03/2026 8:31 PM
"""


def test_multi_page_repair_invoice_and_restaurant_slip(tmp_path, layout):
    inbox = stack(tmp_path, [REPAIR_1, REPAIR_2, RESTAURANT_BILL, RESTAURANT_SLIP, TRADER_JOES])
    rows = plan_batch(inbox, layout, today=TODAY)
    assert documents(rows) == [["0001", "0002"], ["0003", "0004"], ["0005"]]
    by_id = {r["item_id"]: r for r in rows}
    assert "phone 4135550142" in by_id["0001"]["reasons"]
    assert "same store (marigold)" in by_id["0003"]["reasons"]


def test_unreadable_batch_stays_apart(tmp_path, layout):
    inbox = stack(tmp_path, [None, None, None])
    assert documents(plan_batch(inbox, layout, today=TODAY)) == [["0001"], ["0002"], ["0003"]]


def test_second_total_starts_a_new_document(tmp_path, layout):
    inbox = stack(tmp_path, [TRADER_JOES, "Mystery Shop\nWidget 2.00\nTOTAL 2.00"])
    assert documents(plan_batch(inbox, layout, today=TODAY)) == [["0001"], ["0002"]]


def test_survey_deadline_on_the_back_joins_for_review(tmp_path, layout):
    survey = "Tell us how we did\nEnter by 10/20/2026 to win\n"
    inbox = stack(tmp_path, [TRADER_JOES, survey, DUNKIN_MORNING])
    rows = plan_batch(inbox, layout, today=TODAY)
    assert documents(rows) == [["0001", "0002"], ["0003"]]
    assert rows[0]["action"] == "review"


def test_a_receipt_that_opens_with_a_known_store_starts_a_document(tmp_path, layout):
    # No phone, zip, or street under the name, and a date close to the receipt before it.
    marigold = "Marigold\nServer: Dana\nCheck #12\n09/21/2026 7:46 PM\nTotal 40.37\n"
    dunkin = "Dunkin\nRiverton\nName: Alex S\nOrder Number A1234\n09/19/2026\n"
    inbox = stack(tmp_path, [marigold, dunkin])
    assert documents(plan_batch(inbox, layout, today=TODAY)) == [["0001"], ["0002"]]


def test_a_manual_page_sharing_no_words_with_the_last_joins_for_review(tmp_path, layout):
    inbox = stack(tmp_path, [DEWALT_MANUAL, MANUAL_PAGE, "Grill cleaning\nScrub grates after cooling\n"])
    rows = plan_batch(inbox, layout, today=TODAY)
    assert documents(rows) == [["0001", "0002", "0003"]]
    assert rows[0]["action"] == "review" and "shares few words" in rows[0]["reasons"]


def test_one_per_page_turns_grouping_off(tmp_path, layout):
    inbox = stack(tmp_path, [TRADER_JOES, "Thank you for shopping"])
    assert documents(plan_batch(inbox, layout, today=TODAY, group_pages=False)) == [["0001"], ["0002"]]


def test_joined_document_is_one_pdf_and_can_be_split(tmp_path, layout):
    inbox = stack(tmp_path, [TRADER_JOES, "Thank you for shopping", TJ_CARD_SLIP])
    rows = plan_batch(inbox, layout, today=TODAY)
    # The back only joined because it has no header of its own: check it.
    assert rows[0]["action"] == "review" and "weak evidence" in rows[0]["reasons"]
    rows[0]["action"] = "file"
    manifest = tmp_path / "work" / "manifest.csv"
    write_manifest(rows, manifest)
    filed = apply_manifest(manifest, layout, dry_run=True).filed
    assert [(p.item_id, len(p.pages)) for p in filed] == [("0001", 3)]

    # Clearing merge_into files the card slip on its own.
    rows[2].update({"merge_into": "", "action": "file"})
    write_manifest(rows, manifest)
    result = apply_manifest(manifest, layout)
    assert [(p.item_id, len(p.pages)) for p in result.filed] == [("0001", 2), ("0003", 1)]
    assert len(PdfReader(result.filed[0].target).pages) == 2


def test_separator_sheets_split_a_scansmart_stack(tmp_path, layout):
    """One-page ScanSmart PDFs; fake OCR reads the colour patch on each page."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    texts = {(200, 0, 0): "Thank you\nfor visiting", (0, 0, 200): "Thank you\nsee you soon"}

    def page(colour=None):
        image = Image.new("RGB", (300, 400), (245, 245, 245))
        if colour:
            image.paste(colour, (20, 20, 280, 200))
        return image

    for number, colour in enumerate([(200, 0, 0), None, (0, 0, 200)], start=1):
        page(colour).save(inbox / f"Document_20261018_{number:04d}.pdf", "PDF")

    def fake_ocr(image):
        red, _, blue = image.convert("RGB").getpixel((150, 100))
        return texts[(200, 0, 0)] if red > 150 > blue else texts[(0, 0, 200)] if blue > 150 > red else ""

    joined = plan_batch(inbox, layout, today=TODAY, ocr=fake_ocr)
    assert documents(joined) == [["0001", "0002"]]  # blank page dropped, then joined
    separated = plan_batch(inbox, layout, today=TODAY, ocr=fake_ocr, separator_sheets=True)
    assert documents(separated) == [["0001"], ["0002"]]
