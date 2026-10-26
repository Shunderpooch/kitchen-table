from datetime import date

from openpyxl import load_workbook
from PIL import Image
from sqlalchemy import create_engine

from backend.src.scans.pipeline import apply_manifest, plan_batch, undo_apply, write_manifest
from backend.src.scans.text_index import export_xlsx, index_documents, search

from .conftest import TRADER_JOES, scan_with_text

TODAY = date(2026, 10, 18)


def engine_for(tmp_path):
    return create_engine(f"sqlite:///{tmp_path / 'index.db'}")


def scanned_pdf(path, pages=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [Image.new("RGB", (200, 300), (240, 240, 240)) for _ in range(pages)]
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])
    return path


def test_index_search_and_export(tmp_path, layout, onedrive):
    repair = scanned_pdf(onedrive / "Documents" / "cars" / "2009_toyota_rav4" / "2024-04-09_14-15_main_street_auto.pdf")
    texts = {"page1": "Main Street Auto\nBrake pads front 189.00", "page2": "Labor 405.00\nTOTAL 616.00\x01"}
    fake_ocr = iter(texts.values())
    engine = engine_for(tmp_path)

    report = index_documents(engine, layout, lambda image: next(fake_ocr), roots=[repair.parent])
    assert report.indexed == ["Documents/cars/2009_toyota_rav4/2024-04-09_14-15_main_street_auto.pdf"]

    hits = search(engine, '"brake pads"')
    assert [(h.page, h.doc_date) for h in hits] == [(1, date(2024, 4, 9))]
    assert search(engine, "labor")[0].page == 2

    # Unchanged files are not read again.
    again = index_documents(engine, layout, None, roots=[repair.parent])
    assert again.unchanged == 1 and not again.indexed

    out = tmp_path / "text.xlsx"
    assert export_xlsx(engine, out) == (1, 2)
    workbook = load_workbook(out)
    row = dict(zip([c.value for c in workbook["documents"][1]], [c.value for c in workbook["documents"][2]]))
    assert row["kind"] == "car" and row["merchant"] == "main_street_auto" and float(row["ocr_total"]) == 616.0
    assert workbook["pages"].cell(3, 4).value == "Labor 405.00\nTOTAL 616.00"  # control character dropped

    # Deleted files drop out of the index.
    repair.unlink()
    assert index_documents(engine, layout, None, roots=[repair.parent]).removed == 1
    assert not search(engine, "labor")


def never_ocr(image):
    raise AssertionError("text should have been reused, not read again")


def test_text_is_read_once_across_renames_moves_and_redating(tmp_path, layout, onedrive):
    from datetime import datetime

    from backend.src.scans.filedates import set_file_times

    folder = onedrive / "Documents" / "receipts" / "restaurants"
    bill = scanned_pdf(folder / "2026-10-03_marigold.pdf")
    engine = engine_for(tmp_path)
    index_documents(engine, layout, lambda image: "Marigold\nEntree 24.00", roots=[folder])

    set_file_times(bill, datetime(2026, 10, 3, 19, 42))  # re-dated: same content
    assert index_documents(engine, layout, never_ocr, roots=[folder]).unchanged == 1

    renamed = bill.rename(folder.parent / "general" / "2026-10-03_19-42_marigold.pdf")
    report = index_documents(engine, layout, never_ocr, roots=[folder.parent])
    assert report.moved == 1 and not report.indexed and report.removed == 0
    assert search(engine, "entree")[0].path.endswith("general/2026-10-03_19-42_marigold.pdf")

    scanned_pdf(renamed, pages=3)  # content changed: read again
    report = index_documents(engine, layout, lambda image: "rescanned", roots=[folder.parent])
    assert report.indexed and search(engine, "rescanned")


def test_apply_indexes_filed_documents_and_undo_forgets_them(tmp_path, layout, onedrive):
    inbox = tmp_path / "inbox"
    scan_with_text(inbox / "FastFoto_0001.jpg", TRADER_JOES)
    page_texts: dict[str, str] = {}
    rows = plan_batch(inbox, layout, today=TODAY, page_texts=page_texts)
    manifest = tmp_path / "work" / "manifest.csv"
    write_manifest(rows, manifest, page_texts=page_texts)
    engine = engine_for(tmp_path)

    result = apply_manifest(manifest, layout, index_engine=engine)
    assert not result.index_errors
    hit = search(engine, "bananas")[0]
    assert hit.path == "Documents/receipts/grocery/2026-09-26_14-05_trader_joes.pdf"

    undo_apply(result.log_path, index_engine=engine, onedrive=onedrive)
    assert not search(engine, "bananas")
