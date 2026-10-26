"""Optional local OCR backends.

OCR is never required: PDFs with a text layer and sidecar ``.txt`` files work
without it. When available, Tesseract (via pytesseract) is preferred, then
RapidOCR. Everything runs locally.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

OcrEngine = Callable[["object"], str]

_TESSERACT_PATHS = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]


def _tesseract_engine() -> Optional[OcrEngine]:
    try:
        import pytesseract
    except ImportError:
        return None
    binary = shutil.which("tesseract") or next(
        (str(p) for p in _TESSERACT_PATHS if p.exists()), None
    )
    if not binary:
        return None
    pytesseract.pytesseract.tesseract_cmd = binary

    def run(image) -> str:
        return pytesseract.image_to_string(image)

    run.name = "tesseract"
    return run


def _rapidocr_engine() -> Optional[OcrEngine]:
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    reader = RapidOCR()

    def run(image) -> str:
        result, _ = reader(np.asarray(image.convert("RGB")))
        if not result:
            return ""
        # Sort boxes top-to-bottom, then left-to-right, and join into lines.
        boxes = sorted(result, key=lambda r: (round(r[0][0][1] / 15), r[0][0][0]))
        lines: list[str] = []
        last_row = None
        for box, text, _score in boxes:
            row = round(box[0][1] / 15)
            if row == last_row and lines:
                lines[-1] += " " + text
            else:
                lines.append(text)
            last_row = row
        return "\n".join(lines)

    run.name = "rapidocr"
    return run


def get_ocr_engine(preference: str = "auto") -> Optional[OcrEngine]:
    """Return the first available engine, or None when OCR is unavailable."""
    if preference == "none":
        return None
    builders = {"tesseract": _tesseract_engine, "rapidocr": _rapidocr_engine}
    order = [preference] if preference in builders else ["tesseract", "rapidocr"]
    for name in order:
        engine = builders[name]()
        if engine:
            return engine
    return None
