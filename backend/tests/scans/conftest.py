"""Synthetic OneDrive tree and FastFoto batches for scan pipeline tests."""

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.src.scans.conventions import learn_layout


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 placeholder")
    return path


@pytest.fixture
def onedrive(tmp_path: Path) -> Path:
    root = tmp_path / "OneDrive"
    receipts = root / "Documents" / "receipts"
    for name in [
        "grocery/2026-09-27_19-55_trader_joes.pdf",
        "grocery/2026-09-20_18-02_trader_joes.pdf",
        "grocery/2026-09-07_18-07_cvs_pharmacy.pdf",
        "health_care/2026-08-01_cvs_pharmacy.pdf",
        "health_care/2026-08-11_cvs_pharmacy.pdf",
        "cafe/2026-09-10_15-47_dunkin.pdf",
        "restaurants/2026-09-21_19-48_marigold.pdf",
        "gas/2026-09-01_cumberland_farms.pdf",
        "travel/2026-08-27_15-29_copley_garage.pdf",
    ]:
        touch(receipts / name)
    (receipts / "general").mkdir()
    touch(root / "Documents" / "manuals" / "dewalt_dcs334.pdf")
    touch(root / "Documents" / "manuals" / "dewalt_dcw600.pdf")
    touch(root / "Documents" / "manuals" / "installation_guide_fan.pdf")
    touch(root / "Documents" / "hardware_instructions" / "ikea_poang_chair.pdf")
    (root / "Documents" / "cars" / "2012_honda_civic_si").mkdir(parents=True)
    (root / "Documents" / "cars" / "2009_toyota_rav4").mkdir(parents=True)
    (root / "Documents" / "cars" / "1965 Ford Mustang").mkdir(parents=True)

    people = root / "people"
    touch(people / "rivera_sam" / "documents" / "receipts" / "restaurants" / "2026-01-02_marigold.pdf")
    touch(people / "lee_jordan" / "documents" / "receipts" / "fast food" / "2021-06-10_12-56_cook_out.pdf")
    touch(people / "rivera_morgan" / "receipts" / "2021-03-12_14-10_ace_hardware.pdf")
    (people / "ward_casey").mkdir(parents=True)
    (people / "kim_taylor" / "Documents").mkdir(parents=True)
    return root


@pytest.fixture
def layout(onedrive):
    return learn_layout(onedrive)


def scan_image(path: Path, blank: bool = False) -> Path:
    """Write a fake scan: white paper, with dark 'ink' bars unless blank."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (600, 900), (246, 244, 240))
    if not blank:
        draw = ImageDraw.Draw(image)
        for row in range(60, 800, 40):
            draw.rectangle([40, row, 40 + (row * 7) % 480 + 60, row + 14], fill=(20, 20, 20))
    image.save(path, dpi=(300, 300))
    return path


def scan_with_text(path: Path, text: str, blank: bool = False) -> Path:
    scan_image(path, blank=blank)
    path.with_suffix(".txt").write_text(text, encoding="utf-8")
    return path


TRADER_JOES = """TRADER JOE'S
500 Main St Springfield MA
BANANAS 0.29
SUBTOTAL 41.17
TAX 0.00
TOTAL $41.17
VISA ************1234
09/26/2026 14:05
"""

NEW_RESTAURANT = """The Golden Spoon Bistro
7 Elm Street
Server: Dana   Table 12   Guests 2
Entree 24.00
Appetizer 11.00
Subtotal 35.00
Tax 2.19
Total 37.19
Tip ______
Oct 3, 2026 7:42 PM
"""

SAM_PHARMACY = """CVS pharmacy
RX# 449201 Patient: Sam Rivera
Copay 10.00
TOTAL 10.00
VISA approved
08/14/2026 11:20 AM
"""

DEWALT_MANUAL = """DEWALT
DCS356 Oscillating Multi-Tool
INSTRUCTION MANUAL
Safety Instructions
Warranty information
"""

CIVIC_RECEIPT = """AutoZone
Customer vehicle: 2012 Honda Civic Si
Motor Oil 5W-30 34.99
Wiper blades 21.99
TOTAL 56.98
DEBIT approved
10/05/2026 17:35
"""
