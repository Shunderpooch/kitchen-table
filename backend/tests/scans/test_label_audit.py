from datetime import date

from sqlalchemy import create_engine

from backend.src.scans.conventions import learn_layout
from backend.src.scans.label_audit import (
    _name_format, audit_report, label_findings, merchant_variants, record_verdict, review_sample,
    save_findings, text_findings, vote_on_variants,
)
from backend.src.scans.naming import merchant_of, parse_filename
from backend.src.scans.segmentation_eval import Doc

from .conftest import touch

TODAY = date(2026, 10, 18)


def test_numbered_copies_are_the_same_merchant():
    assert merchant_of(parse_filename("2022-04-15_17-48_belles_bread_2.pdf")) == "belles_bread"
    assert merchant_of(parse_filename("2019-12-14_19-06_sephora-2.pdf")) == "sephora"
    assert merchant_of(parse_filename("2025-06-08_12-08_one_65.pdf")) == "one_65"


def test_name_format_suggestions():
    assert _name_format("2022-07-01-15-32_iced_house_mocha.pdf") == "2022-07-01_15-32_iced_house_mocha.pdf"
    assert _name_format("2021-05-24_13_15_bofa_payment.pdf") == "2021-05-24_13-15_bofa_payment.pdf"
    assert _name_format("05-21-2026_-_Service_Authorization.pdf") == "2026-05-21_service_authorization.pdf"
    assert _name_format("2022-10-07_21-45_wendy's.pdf") == "2022-10-07_21-45_wendys.pdf"
    assert _name_format("2022-10-07_21-45_wendys.pdf") is None


def test_merchant_variants():
    variants = merchant_variants({"cvs_pharmacy": 8, "cvs_phramacy": 1, "hmart": 8, "h_mart": 1,
                                  "cvs_1234": 1, "cvs_5678": 1})
    assert variants["cvs_phramacy"][0] == "cvs_pharmacy" and variants["h_mart"][0] == "hmart"
    assert "cvs_5678" not in variants and "cvs_pharmacy" not in variants


def test_label_findings_on_a_tree(onedrive):
    receipts = onedrive / "Documents" / "receipts"
    for name in ["cafe/2026-01-0%d_dunkin.pdf" % i for i in range(1, 5)] + [
        "fast_food/2026-02-01_dunkin.pdf", "cafe/2027-01-01_dunkin.pdf", "gas/2026-03-01_cumberland_farns.pdf",
        "gas/2026-03-02_cumberland_farms.pdf",
    ]:
        touch(receipts / name)
    layout = learn_layout(onedrive)
    found = {(f.path.split("/")[-1], f.check) for f in label_findings(layout, today=TODAY)}
    assert ("2026-02-01_dunkin.pdf", "category_outlier") in found
    assert ("2027-01-01_dunkin.pdf", "implausible_date") in found
    assert ("2026-03-01_cumberland_farns.pdf", "merchant_variant") in found


def test_text_contradictions_and_review_flow(tmp_path, layout):
    docs = [
        Doc("Documents/receipts/grocery/2026-09-27_19-55_trader_joes.pdf", "other_receipts", "dev",
            ["TRADER JOE'S\nTOTAL 41.17\n09/26/2026 19:55"]),           # date off by one day
        Doc("Documents/receipts/grocery/2026-09-20_18-02_trader_joes.pdf", "other_receipts", "dev",
            ["TRADER JOE'S\nTOTAL 12.00\n09/20/2026 06:02 PM"]),        # consistent
        Doc("Documents/receipts/cafe/2026-09-10_15-47_dunkin.pdf", "other_receipts", "dev",
            ["DUNKIN'\n09/10/2026 03:47 AM"]),                          # AM/PM
    ]
    findings = text_findings(layout, docs, today=TODAY)
    by_path = {(f.path.split("/")[-1], f.check): f for f in findings}
    assert "1 day(s) apart" in by_path[("2026-09-27_19-55_trader_joes.pdf", "text_date")].evidence
    assert "AM/PM" in by_path[("2026-09-10_15-47_dunkin.pdf", "text_time")].evidence
    assert not any("2026-09-20" in f.path for f in findings)

    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    assert save_findings(engine, findings) == (2, 0)
    sample = review_sample(engine, ["text_date"], per_check=5)
    record_verdict(engine, sample[0].id, "label_wrong", "slip says 09/26", "claude")
    assert save_findings(engine, findings) == (0, 2)  # rerun keeps the verdict
    report = audit_report(engine)["text_date"]
    assert report["reviewed"] == 1 and report["label_wrong_rate"][0] == 1.0


def test_text_breaks_a_spelling_tie():
    from backend.src.scans.label_audit import Finding

    finding = Finding("a.pdf", "merchant_variant", "machin_thai", "'machim_thai' is used 3 times, 'machin_thai' 1",
                      "", 0.5)
    vote_on_variants([finding], {"a.pdf": ["MACHIM THAI\n12 Main St"]})
    assert "spells it 'machim_thai'" in finding.evidence and finding.strength == 0.95
