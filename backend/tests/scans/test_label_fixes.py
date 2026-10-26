import os
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.src.db.models import LabelFinding, ScannedDocument
from backend.src.scans.label_fixes import _apply_fields, apply_fixes, plan_fixes, undo_fixes
from backend.src.scans.text_index import ensure_schema

from .conftest import touch


def test_apply_fields_keeps_free_form_names():
    assert _apply_fields("1941-04-30-Oldsmobile 76 Bill of Sale.pdf", "1941-05-01", None, None) == (
        "1941-05-01-Oldsmobile 76 Bill of Sale.pdf")
    assert _apply_fields("2021-11-11_02-12_tiltons.pdf", None, "14-12", None) == "2021-11-11_14-12_tiltons.pdf"
    assert _apply_fields("2021-11-11_tiltons.pdf", None, "14-12", None) == "2021-11-11_14-12_tiltons.pdf"
    assert _apply_fields("2021-11-11_14-12_tilton.pdf", None, None, "tiltons") == "2021-11-11_14-12_tiltons.pdf"


def setup(tmp_path, onedrive, findings):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}")
    ensure_schema(engine)
    LabelFinding.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        for path, check, suggestion, decision in findings:
            session.add(LabelFinding(path=path, check=check, suggestion=suggestion, strength=1,
                                     user_decision=decision))
        session.add(ScannedDocument(path=findings[0][0], folder="x", page_count=1))
        session.commit()
    return engine


def test_fixes_rename_move_and_undo(tmp_path, onedrive, layout):
    receipts = onedrive / "Documents" / "receipts"
    a = touch(receipts / "fast_food" / "2022-07-01-15-32_dunkin.pdf")
    dup = touch(receipts / "cafe" / "2026-01-01_copy.pdf")
    keep = touch(receipts / "cafe" / "2026-01-02_fine.pdf")
    engine = setup(tmp_path, onedrive, [
        ("Documents/receipts/fast_food/2022-07-01-15-32_dunkin.pdf", "name_format", "2022-07-01_15-32_dunkin.pdf", "fix"),
        ("Documents/receipts/fast_food/2022-07-01-15-32_dunkin.pdf", "category_outlier", "cafe", "fix"),
        ("Documents/receipts/fast_food/2022-07-01-15-32_dunkin.pdf", "text_time", "15-42", "fix"),
        ("Documents/receipts/cafe/2026-01-01_copy.pdf", "duplicate_content", "", "fix"),
        ("Documents/receipts/cafe/2026-01-02_fine.pdf", "text_date", "2026-01-03", "keep"),
    ])
    plans = {p.source: p for p in plan_fixes(engine, layout)}
    assert plans["Documents/receipts/fast_food/2022-07-01-15-32_dunkin.pdf"].target == (
        "Documents/receipts/cafe/2022-07-01_15-42_dunkin.pdf")
    assert len(plans) == 2  # 'keep' is never applied

    dry = apply_fixes(engine, layout, tmp_path / "logs")
    assert len(dry.applied) == 2 and a.exists()

    result = apply_fixes(engine, layout, tmp_path / "logs", dry_run=False)
    moved = receipts / "cafe" / "2022-07-01_15-42_dunkin.pdf"
    assert moved.exists() and not a.exists() and keep.exists()
    assert (onedrive / "Documents" / "_duplicates" / "Documents" / "receipts" / "cafe" / "2026-01-01_copy.pdf").exists()
    assert not dup.exists()
    assert datetime.fromtimestamp(os.stat(moved).st_mtime) == datetime(2022, 7, 1, 15, 42)
    with Session(engine) as session:
        assert session.scalar(ScannedDocument.__table__.select().with_only_columns(ScannedDocument.path)) == (
            "Documents/receipts/cafe/2022-07-01_15-42_dunkin.pdf")
        assert {r.user_decision for r in session.query(LabelFinding).filter(LabelFinding.check != "text_date")} == {"fixed"}
    assert not plan_fixes(engine, layout)  # nothing left to do

    undone, problems = undo_fixes(engine, layout, result.log_path)
    assert len(undone) == 2 and not problems and a.exists() and dup.exists()


def test_renames_keep_expense_links(tmp_path):
    from backend.src.scans.label_fixes import relink_expenses

    engine = create_engine(f"sqlite:///{tmp_path / 'l.db'}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE expenses (id INTEGER PRIMARY KEY, link TEXT)")
        c.exec_driver_sql("INSERT INTO expenses VALUES (1, '2024-06-10_17-06_sokha_bun.pdf'), (2, 'https://1drv.ms/x')")
    assert relink_expenses(engine, "a/2024-06-10_17-06_sokha_bun.pdf", "a/2024-06-10_14-06_sokha_bun.pdf") == 1
    with engine.begin() as c:
        assert c.exec_driver_sql("SELECT link FROM expenses ORDER BY id").scalars().all() == [
            "2024-06-10_14-06_sokha_bun.pdf", "https://1drv.ms/x"]


def test_undated_name_gets_a_whole_new_name(tmp_path, onedrive, layout):
    receipts = onedrive / "Documents" / "receipts"
    touch(receipts / "general" / "Walmart Receipt 3-3-12 phones.pdf")
    engine = setup(tmp_path, onedrive, [
        ("Documents/receipts/general/Walmart Receipt 3-3-12 phones.pdf", "undated_receipt",
         "2012-03-03_walmart_phones.pdf", "fix"),
        ("Documents/receipts/general/Walmart Receipt 3-3-12 phones.pdf", "text_time", "10-36", "keep"),
    ])
    (plan,) = plan_fixes(engine, layout)
    assert plan.target == "Documents/receipts/general/2012-03-03_walmart_phones.pdf" and not plan.problem


def test_existing_target_is_never_overwritten(tmp_path, onedrive, layout):
    receipts = onedrive / "Documents" / "receipts"
    touch(receipts / "cafe" / "2022-07-01-15-32_x.pdf")
    other = touch(receipts / "cafe" / "2022-07-01_15-32_x.pdf")
    other.write_bytes(b"keep me")
    engine = setup(tmp_path, onedrive, [
        ("Documents/receipts/cafe/2022-07-01-15-32_x.pdf", "name_format", "2022-07-01_15-32_x.pdf", "fix"),
    ])
    result = apply_fixes(engine, layout, tmp_path / "logs", dry_run=False)
    assert not result.applied and "already exists" in result.skipped[0].problem
    assert other.read_bytes() == b"keep me"
