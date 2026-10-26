import pytest

from backend.src.scans.classify import destination_folder
from backend.src.scans.conventions import SELF_KEY


def test_learns_categories_merchants_people_and_cars(layout):
    assert "grocery" in layout.receipt_categories
    assert layout.category_for_merchant("trader_joes")[0] == "grocery"
    # cvs_pharmacy: 2 health_care vs 1 grocery -> majority wins, share recorded.
    category, count, share = layout.category_for_merchant("cvs_pharmacy")
    assert (category, count) == ("health_care", 2)
    assert share == pytest.approx(2 / 3)
    # Merchants filed under people count too, with their category folders normalized.
    assert layout.category_for_merchant("cook_out")[0] == "fast_food"
    assert set(layout.cars) == {"2012_honda_civic_si", "2009_toyota_rav4"}
    assert layout.manual_brands["dewalt"] == 2
    assert "installation" not in layout.manual_brands


def test_resolve_person(layout):
    assert layout.resolve_person("self") == SELF_KEY
    assert layout.resolve_person(None) == SELF_KEY
    assert layout.resolve_person("sam") == "rivera_sam"
    assert layout.resolve_person("Sam Rivera".split()[0]) == "rivera_sam"
    assert layout.resolve_person("rivera_morgan") == "rivera_morgan"
    with pytest.raises(ValueError, match="Ambiguous"):
        layout.resolve_person("rivera")
    with pytest.raises(ValueError, match="Unknown person"):
        layout.resolve_person("nobody")


def test_person_receipt_folders_follow_existing_layout(layout, onedrive):
    people = onedrive / "people"
    # Existing category folder is reused even with a space in its name.
    assert destination_folder(layout, "receipt", "lee_jordan", "fast_food", None, None) == (
        people / "lee_jordan" / "documents" / "receipts" / "fast food"
    )
    # A flat receipts folder stays flat.
    assert destination_folder(layout, "receipt", "rivera_morgan", "grocery", None, None) == (
        people / "rivera_morgan" / "receipts"
    )
    # People without receipts get one inside their existing Documents folder.
    assert destination_folder(layout, "receipt", "kim_taylor", "cafe", None, None) == (
        people / "kim_taylor" / "Documents" / "receipts" / "cafe"
    )
    assert destination_folder(layout, "receipt", SELF_KEY, "cafe", None, None) == (
        onedrive / "Documents" / "receipts" / "cafe"
    )


def test_document_kinds_route_to_documents_folders(layout, onedrive):
    docs = onedrive / "Documents"
    assert destination_folder(layout, "manual", SELF_KEY, None, None, None) == docs / "manuals"
    assert destination_folder(layout, "instructions", SELF_KEY, None, None, None) == docs / "hardware_instructions"
    assert destination_folder(layout, "tax", SELF_KEY, None, None, "2026-01-31") == docs / "taxes" / "2026"
    assert destination_folder(layout, "receipt", SELF_KEY, "gas", "2012_honda_civic_si", None) == (
        docs / "cars" / "2012_honda_civic_si"
    )
    with pytest.raises(ValueError):
        destination_folder(layout, "receipt", SELF_KEY, "gas", "1965_ford_mustang", None)
