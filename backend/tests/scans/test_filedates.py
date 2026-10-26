import os
import sys
from datetime import datetime

import pytest
from pypdf import PdfReader

from backend.src.scans.filedates import document_datetime, restamp, set_file_times, times_match

TODAY = datetime(2026, 10, 18, 12, 0)


def test_document_datetime_from_names():
    assert document_datetime("2026-09-26_14-05_trader_joes.pdf", TODAY) == datetime(2026, 9, 26, 14, 5)
    assert document_datetime("2026-08-28_copley_garage.pdf", TODAY) == datetime(2026, 8, 28, 12, 0)
    assert document_datetime("2026-09-26_14-05_trader_joes_2.pdf", TODAY) == datetime(2026, 9, 26, 14, 5)
    assert document_datetime("2026-03_civic_service_request.pdf", TODAY) == datetime(2026, 3, 1, 12, 0)
    assert document_datetime("dewalt_dcs334.pdf", TODAY) is None
    # Typos that land in the future are left alone.
    assert document_datetime("2028-08-03_springfield_tax_assessor.pdf", TODAY) is None
    assert document_datetime("2026-13-40_bad.pdf", TODAY) is None


def test_set_file_times_sets_modified_and_creation(tmp_path):
    path = tmp_path / "2021-05-26_20-22_harris_teeter.pdf"
    path.write_bytes(b"x")
    when = datetime(2021, 5, 26, 20, 22)
    set_file_times(path, when)
    stat = os.stat(path)
    assert datetime.fromtimestamp(stat.st_mtime) == when
    if sys.platform == "win32":
        assert datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime)) == when
    assert times_match(path, when)


def test_restamp_is_dry_run_by_default_and_idempotent(tmp_path):
    folder = tmp_path / "receipts" / "grocery"
    folder.mkdir(parents=True)
    dated = folder / "2023-12-16_20-10_whole_foods.pdf"
    undated = folder / "notes.pdf"
    for path in (dated, undated):
        path.write_bytes(b"content")
    before = os.stat(dated).st_mtime

    report = restamp([tmp_path])
    assert [p for p, _ in report.changed] == [dated] and report.undated == 1
    assert os.stat(dated).st_mtime == before

    restamp([tmp_path], dry_run=False)
    assert datetime.fromtimestamp(os.stat(dated).st_mtime) == datetime(2023, 12, 16, 20, 10)
    assert dated.read_bytes() == b"content"
    second = restamp([tmp_path], dry_run=False)
    assert not second.changed and second.already_ok == 1


@pytest.mark.usefixtures("onedrive")
def test_apply_stamps_filed_receipts(tmp_path, layout):
    from backend.src.scans.pipeline import apply_manifest, plan_batch, write_manifest

    from .conftest import TRADER_JOES, scan_with_text

    scans = tmp_path / "inbox"
    scan_with_text(scans / "FastFoto_0001.jpg", TRADER_JOES)
    rows = plan_batch(scans, layout, today=TODAY.date())
    manifest = tmp_path / "work" / "manifest.csv"
    write_manifest(rows, manifest)
    target = apply_manifest(manifest, layout).filed[0].target

    assert datetime.fromtimestamp(os.stat(target).st_mtime) == datetime(2026, 9, 26, 14, 5)
    assert PdfReader(target).metadata["/CreationDate"].startswith("D:20260926140500")
