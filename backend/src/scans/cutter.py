"""Cut a scan batch into pages in scan order.

PDFs (Epson ScanSmart): pages are taken in scan order across every PDF in
the folder, so both one stack PDF and ScanSmart's one-PDF-per-page output
become one item per page, or per front/back pair with ``duplex=True``.
Blank pages are dropped; with ``separator_sheets`` a blank sheet also marks
the start of the next document. ``keep_pdfs_whole`` treats each PDF as a
document. ``grouping`` then joins pages into whole documents.

Images (FastFoto app): ``FastFoto_0001.jpg`` (original) pairs with
``FastFoto_0001_b.jpg`` (back); ``FastFoto_0001_a.jpg`` (enhanced copy) is
not filed twice, and near-blank backs are dropped.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
_FASTFOTO_RE = re.compile(r"^(?P<base>.+?)(?:_(?P<suffix>[ab]))?$", re.I)
_NATURAL_RE = re.compile(r"(\d+)")


@dataclass(frozen=True)
class PageRef:
    """A single scanned page: an image file, or one page of a PDF (1-based)."""

    path: Path
    page: Optional[int] = None

    def encode(self) -> str:
        return f"{self.path}#{self.page}" if self.page else str(self.path)

    @classmethod
    def decode(cls, value: str) -> "PageRef":
        value = value.strip()
        match = re.match(r"^(.*\.pdf)#(\d+)$", value, re.I)
        if match:
            return cls(Path(match.group(1)), int(match.group(2)))
        return cls(Path(value))


@dataclass
class ScanItem:
    item_id: str
    pages: list[PageRef]
    text: str = ""
    notes: list[str] = field(default_factory=list)
    starts_document: bool = False  # follows a blank separator sheet
    whole: bool = False  # already one complete document (keep_pdfs_whole)
    page_texts: list[str] = field(default_factory=list)  # one per entry in pages


def _natural_key(path: Path):
    return [int(t) if t.isdigit() else t.lower() for t in _NATURAL_RE.split(path.name)]


def load_page_image(ref: PageRef):
    """Return a PIL image for a page, or None if the PDF page has no image."""
    from PIL import Image

    if ref.page is None:
        image = Image.open(ref.path)
        image.load()
        return image
    from pypdf import PdfReader

    page = PdfReader(str(ref.path)).pages[ref.page - 1]
    images = list(page.images)
    if not images:
        return None
    biggest = max(images, key=lambda img: len(img.data))
    return Image.open(io.BytesIO(biggest.data))


def is_blank(image, ink_threshold: float = 0.004) -> bool:
    """True when almost no pixels are meaningfully darker than the paper."""
    gray = image.convert("L")
    gray.thumbnail((400, 400))
    histogram = gray.histogram()
    total = sum(histogram)
    # Paper brightness: the 90th percentile pixel value.
    running, paper = 0, 255
    for value in range(255, -1, -1):
        running += histogram[value]
        if running >= total * 0.10:
            paper = value
            break
    cutoff = max(0, paper - 60)
    ink = sum(histogram[:cutoff])
    return ink / total < ink_threshold


def _sidecar_text(path: Path) -> Optional[str]:
    sidecar = path.with_suffix(".txt")
    return sidecar.read_text(encoding="utf-8", errors="replace") if sidecar.exists() else None


def page_text(ref: PageRef, ocr: Optional[Callable] = None) -> str:
    return page_text_with_source(ref, ocr)[0]


def page_text_with_source(ref: PageRef, ocr: Optional[Callable] = None) -> tuple[str, str]:
    """(text, source): source is ``pdf_text``, ``sidecar``, ``ocr:<engine>``, or ``none``."""
    if ref.page is None:
        text = _sidecar_text(ref.path)
        if text is not None:
            return text, "sidecar"
    else:
        from pypdf import PdfReader

        text = PdfReader(str(ref.path)).pages[ref.page - 1].extract_text() or ""
        if text.strip():
            return text, "pdf_text"
    if ocr is None:
        return "", "none"
    image = load_page_image(ref)
    return (ocr(image), f"ocr:{getattr(ocr, 'name', 'unknown')}") if image is not None else ("", "none")


def _image_groups(images: list[Path]) -> list[tuple[Path, Optional[Path], list[str]]]:
    groups: dict[str, dict[str, Path]] = {}
    order: list[str] = []
    for path in images:
        match = _FASTFOTO_RE.match(path.stem)
        base, suffix = match.group("base"), (match.group("suffix") or "").lower()
        if base not in groups:
            groups[base] = {}
            order.append(base)
        groups[base][suffix] = path
    result = []
    for base in order:
        parts = groups[base]
        notes = []
        front = parts.get("") or parts.get("a")
        if front is None:
            # Only a back side exists; file it on its own rather than drop it.
            front, back = parts["b"], None
            notes.append("back side without a front")
        else:
            back = parts.get("b")
        if "" in parts and "a" in parts:
            notes.append(f"enhanced copy {parts['a'].name} not filed (original kept)")
        result.append((front, back, notes))
    return result


def discover_items(
    input_dir: Path,
    duplex: bool = False,
    keep_pdfs_whole: bool = False,
    drop_blank_backs: bool = True,
    ocr: Optional[Callable] = None,
    separator_sheets: bool = False,
) -> list[ScanItem]:
    from pypdf import PdfReader

    input_dir = Path(input_dir)
    files = sorted((p for p in input_dir.iterdir() if p.is_file()), key=_natural_key)
    images = [p for p in files if p.suffix.lower() in IMAGE_EXTENSIONS]
    pdfs = [p for p in files if p.suffix.lower() == ".pdf"]

    raw: list[ScanItem] = []
    separated = False

    def add(pages: list[PageRef], notes: list[str], whole: bool = False) -> None:
        nonlocal separated
        raw.append(ScanItem("", pages, notes=notes, starts_document=separated, whole=whole))
        separated = False

    for front, back, notes in _image_groups(images):
        if separator_sheets and is_blank(load_page_image(PageRef(front))):
            separated = True
            continue
        pages = [PageRef(front)]
        if back is not None:
            if drop_blank_backs and is_blank(load_page_image(PageRef(back))):
                notes.append(f"blank back {back.name} dropped")
            else:
                pages.append(PageRef(back))
        add(pages, notes)

    # Pages run in scan order across files, so duplex pairing works both for
    # one stack PDF and for ScanSmart's one-PDF-per-page output.
    pdf_pages: list[PageRef] = []
    for pdf in pdfs:
        refs = [PageRef(pdf, n) for n in range(1, len(PdfReader(str(pdf)).pages) + 1)]
        if keep_pdfs_whole:
            add(refs, [], whole=True)
        else:
            pdf_pages.extend(refs)

    step = 2 if duplex else 1
    for start in range(0, len(pdf_pages), step):
        group, notes = [], []
        for offset, ref in enumerate(pdf_pages[start:start + step]):
            image = load_page_image(ref)
            blank = image is not None and is_blank(image)
            if blank and (offset > 0 or not duplex or separator_sheets) and drop_blank_backs:
                notes.append(f"blank page {ref.page} of {ref.path.name} dropped")
                continue
            group.append(ref)
        if group:
            add(group, notes)
        elif separator_sheets:
            separated = True

    for index, item in enumerate(raw, start=1):
        item.item_id = f"{index:04d}"
        item.page_texts = [page_text(ref, ocr) for ref in item.pages]
        item.text = "\n".join(item.page_texts)
    return raw


def write_pdf(
    pages: list[PageRef],
    destination: Path,
    jpeg_quality: int = 85,
    document_date: Optional[datetime] = None,
) -> None:
    """Write pages into one PDF. Image pages are JPEG-compressed; PDF pages are copied.

    ``document_date`` becomes the PDF's internal creation/modification date.
    """
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    if document_date:
        stamp = document_date.strftime("D:%Y%m%d%H%M%S")
        writer.add_metadata({"/CreationDate": stamp, "/ModDate": stamp})
    for ref in pages:
        if ref.page is not None:
            writer.add_page(PdfReader(str(ref.path)).pages[ref.page - 1])
            continue
        image = load_page_image(ref).convert("RGB")
        dpi = image.info.get("dpi", (300, 300))[0] or 300
        buffer = io.BytesIO()
        image.save(buffer, "PDF", resolution=float(dpi), quality=jpeg_quality)
        buffer.seek(0)
        for page in PdfReader(buffer).pages:
            writer.add_page(page)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    with open(partial, "wb") as handle:
        writer.write(handle)
    if destination.exists():
        partial.unlink()
        raise FileExistsError(f"Refusing to overwrite {destination}")
    partial.rename(destination)
