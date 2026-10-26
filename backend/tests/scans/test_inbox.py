import json
import os
import time

from backend.src.scans.inbox import inbox_tick
from backend.src.scans.pipeline import read_manifest

from .conftest import SAM_PHARMACY, CIVIC_RECEIPT, TRADER_JOES, scan_with_text


def age(path, seconds=600):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_inbox_scans_become_review_batches(layout, onedrive, tmp_path):
    inbox = onedrive / "Scans" / "Inbox"
    for name, body in (("Document_0001.jpg", TRADER_JOES), ("Document_0002.jpg", CIVIC_RECEIPT)):
        age(scan_with_text(inbox / name, body))
    age(scan_with_text(inbox / "Sam" / "Document_0001.jpg", SAM_PHARMACY))
    scans_dir = tmp_path / "data" / "scans"

    result = inbox_tick(layout, scans_dir, ocr=None)
    assert len(result.batches) == 2 and not result.errors
    assert not any(p.is_file() for p in inbox.rglob("*"))  # moved out of the inbox

    mine, sams = (scans_dir / b for b in result.batches)
    info = json.loads((mine / "batch.json").read_text())
    assert info["files"] == ["Document_0001.jpg", "Document_0002.jpg"] and info["person"] == "self"
    assert sorted(p.name for p in (onedrive / "Scans" / "Originals" / mine.name).iterdir())[0] == "Document_0001.jpg"
    assert (mine / "plan.done").read_text() == "0"
    pages = json.loads((mine / "pages.json").read_text())
    assert [p["n"] for p in pages] == [1, 2] and (mine / "pages" / "0001_t.jpg").exists()
    assert {r["person"] for r in read_manifest(sams / "manifest.csv")} == {"rivera_sam"}


def test_inbox_waits_for_a_scan_in_progress_and_when_paused(layout, onedrive, tmp_path):
    inbox = onedrive / "Scans" / "Inbox"
    scan_with_text(inbox / "Document_0001.jpg", TRADER_JOES)  # just written
    scans_dir = tmp_path / "scans"
    result = inbox_tick(layout, scans_dir, ocr=None)
    assert result.batches == [] and result.waiting == "a scan is still arriving"
    age(inbox / "Document_0001.jpg")
    scans_dir.mkdir(parents=True, exist_ok=True)
    (scans_dir / "PAUSED").touch()
    assert inbox_tick(layout, scans_dir, ocr=None).waiting.startswith("paused")
    (scans_dir / "PAUSED").unlink()
    assert len(inbox_tick(layout, scans_dir, ocr=None).batches) == 1
