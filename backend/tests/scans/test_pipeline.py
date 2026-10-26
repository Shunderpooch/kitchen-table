import csv
from datetime import date

import pytest
from PIL import Image
from pypdf import PdfReader

from backend.src.scans.cutter import discover_items, is_blank
from backend.src.scans.pipeline import (
    apply_manifest,
    plan_batch,
    read_manifest,
    undo_apply,
    write_manifest,
)

from .conftest import (
    DEWALT_MANUAL,
    SAM_PHARMACY,
    NEW_RESTAURANT,
    CIVIC_RECEIPT,
    TRADER_JOES,
    scan_image,
    scan_with_text,
)

TODAY = date(2026, 10, 18)


@pytest.fixture
def batch(tmp_path):
    scans = tmp_path / "FastFoto"
    scan_with_text(scans / "FastFoto_0001.jpg", TRADER_JOES)
    scan_image(scans / "FastFoto_0001_a.jpg")  # enhanced copy: not filed twice
    scan_image(scans / "FastFoto_0001_b.jpg", blank=True)  # blank back: dropped
    scan_with_text(scans / "FastFoto_0002.jpg", NEW_RESTAURANT)
    scan_image(scans / "FastFoto_0002_b.jpg")  # printed back: kept
    scan_with_text(scans / "FastFoto_0003.jpg", SAM_PHARMACY)
    scan_with_text(scans / "FastFoto_0004.jpg", DEWALT_MANUAL)
    scan_with_text(scans / "FastFoto_0005.jpg", CIVIC_RECEIPT)
    scan_with_text(scans / "FastFoto_0006.jpg", "RETURN POLICY\nKeep this receipt")  # 2nd half of 0005
    return scans


def rows_by_id(rows):
    return {row["item_id"]: row for row in rows}


def test_blank_detection(tmp_path):
    assert is_blank(Image.new("RGB", (100, 100), (250, 250, 250)))
    assert is_blank(Image.open(scan_image(tmp_path / "blank.png", blank=True)))
    assert not is_blank(Image.open(scan_image(tmp_path / "inked.png")))


def test_cutter_pairs_fronts_backs_and_drops_blanks(batch):
    items = discover_items(batch)
    assert [len(i.pages) for i in items] == [1, 2, 1, 1, 1, 1]
    assert items[0].pages[0].path.name == "FastFoto_0001.jpg"
    assert any("blank back" in n for n in items[0].notes)
    assert any("enhanced copy" in n for n in items[0].notes)


def test_cutter_splits_stack_pdf(tmp_path):
    pages = [Image.new("RGB", (300, 400), (245, 245, 245)) for _ in range(4)]
    for page in (pages[0], pages[2]):
        page.paste((10, 10, 10), (20, 20, 280, 200))
    stack = tmp_path / "in" / "stack.pdf"
    stack.parent.mkdir()
    pages[0].save(stack, "PDF", save_all=True, append_images=pages[1:])
    simplex = discover_items(stack.parent)
    assert [[p.page for p in i.pages] for i in simplex] == [[1], [3]]
    duplex = discover_items(stack.parent, duplex=True)
    assert [[p.page for p in i.pages] for i in duplex] == [[1], [3]]
    assert len(discover_items(stack.parent, keep_pdfs_whole=True)) == 1


def test_cutter_pairs_scansmart_single_page_pdfs(tmp_path):
    """ScanSmart saves one PDF per page: Document_<date>_0001.pdf, _0002.pdf, ..."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    inked = Image.new("RGB", (300, 400), (245, 245, 245))
    inked.paste((10, 10, 10), (20, 20, 280, 200))
    blank = Image.new("RGB", (300, 400), (245, 245, 245))
    # front, printed back, front, blank back; names sort naturally past 0009.
    for number, page in [(9, inked), (10, inked), (11, inked), (12, blank)]:
        page.save(inbox / f"Document_20261018_{number:04d}.pdf", "PDF")
    duplex = discover_items(inbox, duplex=True)
    assert [[p.path.name[-8:-4] for p in i.pages] for i in duplex] == [["0009", "0010"], ["0011"]]
    assert any("blank page" in n for n in duplex[1].notes)
    assert len(discover_items(inbox)) == 3


def test_raw_scanner_names_are_not_learned_as_merchants(onedrive):
    from backend.src.scans.conventions import learn_layout

    cafe = onedrive / "Documents" / "receipts" / "cafe"
    (cafe / "Document_20261018_0001.pdf").write_bytes(b"x")
    assert not any(m.startswith("document") for m in learn_layout(onedrive).merchant_categories)


def test_plan_names_and_routes(batch, layout):
    rows = rows_by_id(plan_batch(batch, layout, today=TODAY))

    tj = rows["0001"]
    assert tj["filename"] == "2026-09-26_14-05_trader_joes.pdf"
    assert tj["destination"].replace("\\", "/") == "Documents/receipts/grocery"
    assert tj["action"] == "file"
    assert tj["total"] == "41.17"

    bistro = rows["0002"]
    assert bistro["filename"] == "2026-10-03_19-42_the_golden_spoon_bistro.pdf"
    assert bistro["category"] == "restaurants"
    assert bistro["action"] == "review"  # new merchant: needs a human look

    pharmacy = rows["0003"]
    assert pharmacy["category"] == "health_care"
    assert "rivera_sam" in pharmacy["reasons"]
    assert pharmacy["action"] == "review"

    manual = rows["0004"]
    assert manual["kind"] == "manual"
    assert manual["filename"] == "dewalt_dcs356.pdf"
    assert manual["destination"].replace("\\", "/") == "Documents/manuals"

    civic = rows["0005"]
    assert civic["car"] == "2012_honda_civic_si"
    assert civic["destination"].replace("\\", "/") == "Documents/cars/2012_honda_civic_si"
    assert rows["0006"]["merge_into"] == "0005"  # second piece joined automatically
    assert "joined 0006" in civic["reasons"]
    assert not any(rows[i]["merge_into"] for i in ("0001", "0002", "0003", "0004", "0005"))


def test_plan_flags_rescans_as_possible_duplicates(batch, layout, onedrive):
    (onedrive / "Documents" / "receipts" / "grocery" / "2026-09-26_14-05_trader_joes.pdf").write_bytes(b"x")
    row = rows_by_id(plan_batch(batch, layout, today=TODAY))["0001"]
    assert row["action"] == "review"
    assert row["filename"] == "2026-09-26_14-05_trader_joes_2.pdf"
    assert "possible duplicate" in row["reasons"]


def test_plan_for_person_batch(batch, layout):
    rows = rows_by_id(plan_batch(batch, layout, person="sam", today=TODAY))
    assert rows["0003"]["person"] == "rivera_sam"
    assert "check person" not in rows["0003"]["reasons"]
    assert rows["0001"]["destination"].replace("\\", "/") == "people/rivera_sam/documents/receipts/grocery"


def edit_and_apply(tmp_path, layout, rows, edits, dry_run=False):
    by_id = rows_by_id(rows)
    for item_id, changes in edits.items():
        by_id[item_id].update(changes)
    manifest = tmp_path / "work" / "manifest.csv"
    write_manifest(rows, manifest)
    return manifest, apply_manifest(manifest, layout, dry_run=dry_run)


def test_apply_files_copies_merges_and_undoes(batch, layout, tmp_path, onedrive):
    rows = plan_batch(batch, layout, today=TODAY)
    manifest, result = edit_and_apply(tmp_path, layout, rows, {
        "0002": {"action": "file", "merchant": "Golden Spoon"},
        "0003": {"action": "file", "person": "sam"},
        "0004": {"action": "skip"},
        "0005": {"action": "file", "car": ""},
    })
    targets = {p.item_id: p.target for p in result.filed}
    receipts = onedrive / "Documents" / "receipts"
    assert targets["0001"] == receipts / "grocery" / "2026-09-26_14-05_trader_joes.pdf"
    assert targets["0002"] == receipts / "restaurants" / "2026-10-03_19-42_golden_spoon.pdf"
    assert targets["0003"] == (
        onedrive / "people" / "rivera_sam" / "documents" / "receipts" / "health_care"
        / "2026-08-14_11-20_cvs_pharmacy.pdf"
    )
    assert result.skipped == ["0004"] and result.merged == ["0006"]

    assert len(PdfReader(targets["0002"]).pages) == 2  # front + printed back
    assert len(PdfReader(targets["0005"]).pages) == 2  # merged long receipt
    assert (batch / "FastFoto_0001.jpg").exists()  # originals untouched

    # Applying again never overwrites: new copies get a numeric suffix.
    second = apply_manifest(manifest, layout)
    assert {p.target.name for p in second.filed} >= {"2026-09-26_14-05_trader_joes_2.pdf"}

    removed, kept = undo_apply(second.log_path)
    assert len(removed) == len(second.filed) and not kept
    removed, kept = undo_apply(result.log_path)
    assert not targets["0001"].exists()
    assert not kept
    # Folders created by apply are cleaned up; pre-existing folders stay.
    assert (receipts / "grocery").is_dir()
    assert not (onedrive / "people" / "rivera_sam" / "documents" / "receipts" / "health_care").exists()


def test_apply_validates_everything_before_writing(batch, layout, tmp_path, onedrive):
    rows = plan_batch(batch, layout, today=TODAY)
    before = sorted(p for p in onedrive.rglob("*"))
    with pytest.raises(ValueError) as excinfo:
        edit_and_apply(tmp_path, layout, rows, {
            "0002": {"action": "file", "category": "not_a_folder"},
            "0003": {"action": "file", "person": "nobody"},
            "0004": {"action": "file", "filename_override": "../../escape.pdf"},
            "0005": {"action": "maybe"},
        })
    message = str(excinfo.value)
    for fragment in ["0002", "0003", "0004", "0005", "nothing was written"]:
        assert fragment in message
    assert sorted(p for p in onedrive.rglob("*")) == before


def test_dry_run_writes_nothing(batch, layout, tmp_path, onedrive):
    rows = plan_batch(batch, layout, today=TODAY)
    before = sorted(onedrive.rglob("*"))
    _, result = edit_and_apply(tmp_path, layout, rows, {}, dry_run=True)
    assert result.filed and result.log_path is None
    assert sorted(onedrive.rglob("*")) == before


def test_manifest_round_trips_through_excel_encoding(batch, layout, tmp_path):
    rows = plan_batch(batch, layout, today=TODAY)
    path = tmp_path / "m.csv"
    write_manifest(rows, path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert read_manifest(path)[0]["filename"] == rows[0]["filename"]
    with open(path, newline="", encoding="utf-8-sig") as handle:
        assert "item_id" in next(csv.reader(handle))
