from datetime import date

from sqlalchemy import create_engine

from backend.src.scans.naming_eval import evaluate_naming, held_out, labels_for, wilson
from backend.src.scans.segmentation_eval import load_census
from backend.src.scans.text_index import index_documents

from .conftest import TRADER_JOES
from .test_text_index import scanned_pdf

TODAY = date(2026, 10, 18)


def test_labels_come_from_folders_and_names(layout):
    receipt = labels_for(layout, "Documents/receipts/grocery/2026-09-27_19-55_trader_joes.pdf")
    assert (receipt.kind, receipt.category, receipt.date, receipt.time, receipt.merchant) == (
        "receipt", "grocery", "2026-09-27", "19-55", "trader_joes")
    person = labels_for(layout, "people/lee_jordan/documents/receipts/fast food/2021-06-10_12-56_cook_out.pdf")
    assert (person.person, person.category) == ("lee_jordan", "fast_food")
    flat = labels_for(layout, "people/rivera_morgan/receipts/2021-03-12_14-10_ace_hardware.pdf")
    assert flat.category is None
    car = labels_for(layout, "Documents/cars/2009_toyota_rav4/service/2024-04-09_main_street_auto.pdf")
    assert (car.kind, car.car, car.time, car.merchant) == ("car", "2009_toyota_rav4", None, None)
    assert car.folder == "documents/cars/2009_toyota_rav4"
    assert labels_for(layout, "Documents/manuals/dewalt_dcs334.pdf").kind == "manual"


def test_held_out_removes_and_restores_history(layout):
    labels = labels_for(layout, "Documents/receipts/cafe/2026-09-10_15-47_dunkin.pdf")
    before = {k: v.copy() for k, v in layout.merchant_categories.items()}
    with held_out(layout, labels, "x"):
        assert "dunkin" not in layout.merchant_categories  # its only filing
        assert "dunkin" not in layout.known_merchants()
    assert layout.merchant_categories == before

    tj = labels_for(layout, "Documents/receipts/grocery/2026-09-27_19-55_trader_joes.pdf")
    with held_out(layout, tj, "x"):
        assert layout.merchant_categories["trader_joes"]["grocery"] == 1
    assert layout.merchant_categories["trader_joes"]["grocery"] == 2


def test_wilson_interval():
    p, lo, hi = wilson(45, 50)
    assert p == 0.9 and 0.78 < lo < 0.80 and 0.95 < hi < 0.97


def test_census_naming_end_to_end(tmp_path, layout, onedrive):
    folder = onedrive / "Documents" / "receipts" / "grocery"
    scanned_pdf(folder / "2026-09-26_14-05_trader_joes.pdf", pages=1)
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}")
    index_documents(engine, layout, lambda image: TRADER_JOES, roots=[folder])
    # Re-learn so the new file is part of history, as in real OneDrive.
    from backend.src.scans.conventions import learn_layout

    layout = learn_layout(onedrive)
    docs, _ = load_census(engine, layout, roots=[folder])
    report = evaluate_naming(layout, docs, today=TODAY)
    assert report.documents == 1
    for name in ("date", "time", "merchant", "category", "folder", "filename + folder"):
        assert report.fields[name].correct == 1, name
    assert report.fields["merchant (seen before)"].n == 1
