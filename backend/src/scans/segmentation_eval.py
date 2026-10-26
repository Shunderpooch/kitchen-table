"""Evaluate page joining as a document segmentation problem.

Ground truth comes from documents already filed in OneDrive: each filed PDF
is one document, so splitting PDFs into pages and shuffling the documents
gives a scanned "stack" with known boundaries.

- Corpus: the census of every document in the text index (``scans
  index-text``) under the roots: receipts, cars, manuals, instructions, and
  people's receipts. Only the database is read, so reruns are cheap, and
  coverage (indexed vs files on disk) is reported. Results are broken down
  by folder and by document length.
- Split: ``sha1(path) % 5 == 0`` is test, the rest is dev. Tune on dev only.
- Held out: each document's own merchant is removed from filing history
  (one filing fewer) before its pages are read.
- Metrics, with page gaps as the unit (positive = a new document starts):
  precision, recall, F1, Pk and WindowDiff; document exact-match F1; and for
  the review flag, how many wrong joins it catches and how many documents it
  sends to review.
- Uncertainty: documents are resampled with replacement and re-shuffled into
  a new stack per bootstrap round; 95% percentile intervals.
- Baselines: one document per page, and strong joins only.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from .conventions import OneDriveLayout
from .cutter import PageRef, ScanItem
from .extract import extract_fields
from .grouping import Unit, group_units, make_unit
from .naming import merchant_of, parse_filename

SYSTEMS = ("one_per_page", "strong_only", "rules")


@dataclass
class Doc:
    key: str  # path relative to OneDrive
    stratum: str  # cars / restaurants / other receipts
    split: str  # dev / test
    pages: list[str]


def _stratum(rel: str) -> str:
    parts = [p.lower() for p in rel.replace("\\", "/").split("/")]
    if parts[0] == "people":
        return "people"
    for folder, name in (("cars", "cars"), ("manuals", "manuals"),
                         ("hardware_instructions", "instructions"), ("restaurants", "restaurants")):
        if folder in parts:
            return name
    return "other_receipts"


def length_bucket(pages: int) -> str:
    if pages <= 2:
        return f"{pages} page{'s' if pages > 1 else ''}"
    return "3-4 pages" if pages <= 4 else "5-8 pages" if pages <= 8 else "9+ pages"


def split_of(rel: str) -> str:
    return "test" if int(hashlib.sha1(rel.replace("\\", "/").encode()).hexdigest(), 16) % 5 == 0 else "dev"


@dataclass
class Coverage:
    files: int  # PDFs on disk under the roots
    indexed: int  # of those, with stored text
    no_text: int  # indexed, but no page has any text


def load_census(
    engine, layout: OneDriveLayout, roots: Optional[Iterable[Path]] = None
) -> tuple[list[Doc], Coverage]:
    """Every indexed document under the roots. Reads the database, never the files."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from ..db.models import ScannedDocument
    from .text_index import default_roots, ensure_schema

    ensure_schema(engine)
    roots = list(roots or default_roots(layout))
    on_disk = {
        str(p.relative_to(layout.onedrive)).replace("\\", "/")
        for root in roots for p in Path(root).rglob("*.pdf")
    }
    docs, no_text = [], 0
    with Session(engine) as session:
        for row in session.scalars(select(ScannedDocument).order_by(ScannedDocument.path)):
            if row.path not in on_disk or not row.pages:
                continue
            pages = [p.text for p in row.pages]
            no_text += not any(t.strip() for t in pages)
            docs.append(Doc(row.path, _stratum(row.path), split_of(row.path), pages))
    return docs, Coverage(len(on_disk), len(docs), no_text)


def doc_units(docs: list[Doc], layout: OneDriveLayout) -> dict[str, list[Unit]]:
    """Units per document, each read with its own filing removed from history."""
    known_all = Counter(layout.known_merchants())
    names = layout.name_slugs()
    result = {}
    for doc in docs:
        slug = parse_filename(Path(doc.key).name).slug
        known = dict(known_all)
        if slug in known:
            known[slug] -= 1
            if known[slug] <= 0:
                del known[slug]
        units = []
        for page, text in enumerate(doc.pages, start=1):
            item = ScanItem(f"{page:04d}", [PageRef(Path(doc.key), page)], text=text)
            extracted = extract_fields(text, known, skip_names=names, phone_book=layout.phone_book,
                                       leave_out=merchant_of(parse_filename(Path(doc.key).name)))
            units.append(make_unit(item, extracted, layout))
        result[doc.key] = units
    return result


# ---------------------------------------------------------------- metrics


def _labels_from_groups(groups) -> tuple[list[int], list[bool]]:
    """(segment id per page, weak flag per page gap that was joined)."""
    seg, weak_gap = [], []
    for g, group in enumerate(groups):
        weak_ids = {r.split(":")[0].split()[1] for r in group.reasons if "weak evidence" in r}
        for k, unit in enumerate(group.units):
            seg.append(g)
            if seg[:-1]:
                weak_gap.append(k > 0 and unit.item.item_id in weak_ids)
    return seg, weak_gap


def _pk(ref: list[int], hyp: list[int], k: int) -> float:
    n = len(ref) - k
    if n <= 0:
        return 0.0
    return sum((ref[i] == ref[i + k]) != (hyp[i] == hyp[i + k]) for i in range(n)) / n


def _windowdiff(ref_b: list[int], hyp_b: list[int], k: int) -> float:
    """``*_b``: 1 where a boundary follows page i (gap i)."""
    n = len(ref_b) - k + 1
    if n <= 0:
        return 0.0
    return sum(sum(ref_b[i:i + k]) != sum(hyp_b[i:i + k]) for i in range(n)) / n


def _segments(seg: list[int]) -> set[tuple[int, int]]:
    spans, start = set(), 0
    for i in range(1, len(seg) + 1):
        if i == len(seg) or seg[i] != seg[i - 1]:
            spans.add((start, i))
            start = i
    return spans


def _f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def score_stack(ref: list[int], hyp: list[int], weak_gap: list[bool], k: int) -> dict:
    ref_b = [int(ref[i + 1] != ref[i]) for i in range(len(ref) - 1)]
    hyp_b = [int(hyp[i + 1] != hyp[i]) for i in range(len(hyp) - 1)]
    tp = sum(r and h for r, h in zip(ref_b, hyp_b))
    fp = sum(h and not r for r, h in zip(ref_b, hyp_b))
    fn = sum(r and not h for r, h in zip(ref_b, hyp_b))
    tn = len(ref_b) - tp - fp - fn
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    # Join class (positive = pages belong together), what the user cares about.
    jp = tn / (tn + fn) if tn + fn else 1.0
    jr = tn / (tn + fp) if tn + fp else 1.0
    true_docs, pred_docs = _segments(ref), _segments(hyp)
    exact = len(true_docs & pred_docs)
    dp, dr = exact / len(pred_docs), exact / len(true_docs)
    wrong_joins = [i for i, (rb, hb) in enumerate(zip(ref_b, hyp_b)) if rb and not hb]
    caught = sum(weak_gap[i] for i in wrong_joins)
    flagged_docs = len({hyp[i + 1] for i, w in enumerate(weak_gap) if w})
    return {
        "boundary_precision": p, "boundary_recall": r, "boundary_f1": _f1(p, r),
        "join_precision": jp, "join_recall": jr, "join_f1": _f1(jp, jr),
        "pk": _pk(ref, hyp, k), "windowdiff": _windowdiff(ref_b, hyp_b, k),
        "doc_exact_f1": _f1(dp, dr),
        "boundary_tp": tp, "boundary_fp": fp, "boundary_fn": fn, "boundary_tn": tn,
        "wrong_joins": len(wrong_joins),
        "wrong_joins_caught_by_review": caught / len(wrong_joins) if wrong_joins else 1.0,
        "review_rate": flagged_docs / len(pred_docs),
    }


def _predict(units: list[Unit], system: str) -> tuple[list[int], list[bool]]:
    if system == "one_per_page":
        return list(range(len(units))), [False] * (len(units) - 1)
    groups = group_units(units)
    if system == "strong_only":
        # Weak joins become boundaries.
        split_units = []
        for group in groups:
            weak_ids = {r.split(":")[0].split()[1] for r in group.reasons if "weak evidence" in r}
            split_units.append([group.units[0]])
            for unit in group.units[1:]:
                if unit.item.item_id in weak_ids:
                    split_units.append([unit])
                else:
                    split_units[-1].append(unit)
        seg = [g for g, us in enumerate(split_units) for _ in us]
        return seg, [False] * (len(units) - 1)
    return _labels_from_groups(groups)


def _stack(order: list[Doc], units_by_doc: dict[str, list[Unit]]):
    """Pages of ``order`` (already shuffled) as one scanned stack, with the true document per page."""
    units, ref = [], []
    for d, doc in enumerate(order):
        for unit in units_by_doc[doc.key]:
            # Unique ids across the stack; grouping reasons refer to them.
            item = ScanItem(f"{len(units) + 1:05d}", unit.item.pages, text=unit.item.text)
            units.append(Unit(item, unit.extracted, unit.kind, unit.manual, unit.marker,
                              unit.title, unit.header, unit.phones))
            ref.append(d)
    return units, ref


OUTCOMES = ("exact", "split", "merged", "split_and_merged")


def doc_outcomes(ref: list[int], hyp: list[int]) -> list[str]:
    """Per true document: rebuilt exactly, split apart, merged with another document, or both."""
    pages_of: dict[int, list[int]] = {}
    members: dict[int, set[int]] = {}
    for i, (d, h) in enumerate(zip(ref, hyp)):
        pages_of.setdefault(d, []).append(i)
        members.setdefault(h, set()).add(d)
    result = []
    for d in sorted(pages_of):
        segments = {hyp[i] for i in pages_of[d]}
        split = len(segments) > 1
        merged = any(len(members[s]) > 1 for s in segments)
        result.append("split_and_merged" if split and merged else "split" if split else
                      "merged" if merged else "exact")
    return result


def _interval(values: list[float], point: float) -> tuple[float, float, float]:
    values = sorted(values) or [point]
    return point, values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def _date_windows(docs: list[Doc], rng: random.Random, window: int = 12) -> list[list[Doc]]:
    """Like real scan sessions: documents in date order, shuffled within small windows
    (a stack is a few weeks of receipts, so neighbours often have close dates).
    Bootstrap rounds resample whole windows, so no document sits next to a copy of itself."""
    def day(doc: Doc) -> str:
        return parse_filename(Path(doc.key).name).date or ""

    ordered = sorted(docs, key=lambda d: (day(d), rng.random()))
    windows = []
    for start in range(0, len(ordered), window):
        chunk = ordered[start:start + window]
        rng.shuffle(chunk)
        windows.append(chunk)
    return windows


def evaluate_segmentation(
    docs: list[Doc],
    units_by_doc: dict[str, list[Unit]],
    rounds: int = 200,
    seed: int = 1,
    order: str = "shuffled",
) -> dict:
    """Round 0 scores the whole set in one stack; rounds 1.. resample documents for 95% CIs.

    ``order``: ``shuffled`` (neighbours are unrelated) or ``date`` (neighbours
    have close dates, as in a real stack; the harder case for joining).

    ``breakdown`` gives, for the rules system, per-document outcome rates by
    folder and by document length, measured inside the full mixed stack.
    """
    rng = random.Random(seed)
    pages = sum(len(d.pages) for d in docs)
    k = max(2, round(pages / len(docs) / 2))  # half the mean document length, as usual for Pk
    samples: dict[str, list[dict]] = {s: [] for s in SYSTEMS}
    groupings = {"folder": lambda d: d.stratum, "length": lambda d: length_bucket(len(d.pages))}
    # breakdown[grouping][value][outcome] -> per-round rates
    rates: dict = {g: {} for g in groupings}
    sizes: dict = {g: Counter(fn(d) for d in docs) for g, fn in groupings.items()}
    windows = _date_windows(docs, rng) if order == "date" else []
    for b in range(rounds):
        if order == "date":
            picked = windows if b == 0 else [rng.choice(windows) for _ in windows]
            stacked = [doc for window in picked for doc in window]
        else:
            stacked = docs[:] if b == 0 else [rng.choice(docs) for _ in docs]
            rng.shuffle(stacked)
        units, ref = _stack(stacked, units_by_doc)
        for system in SYSTEMS:
            hyp, weak_gap = _predict(units, system)
            samples[system].append(score_stack(ref, hyp, weak_gap, k))
            if system != "rules":
                continue
            outcomes = doc_outcomes(ref, hyp)
            for grouping, fn in groupings.items():
                tally: dict[str, Counter] = {}
                for doc, outcome in zip(stacked, outcomes):
                    tally.setdefault(fn(doc), Counter())[outcome] += 1
                for value, counts in tally.items():
                    total = sum(counts.values())
                    slot = rates[grouping].setdefault(value, {o: [] for o in OUTCOMES})
                    for outcome in OUTCOMES:
                        slot[outcome].append(counts[outcome] / total)

    report = {"documents": len(docs), "pages": pages, "multi_page": sum(len(d.pages) > 1 for d in docs),
              "k": k, "rounds": rounds, "systems": {}, "breakdown": {}}
    for system, rows in samples.items():
        report["systems"][system] = {
            name: _interval([r[name] for r in rows[1:]], rows[0][name]) for name in rows[0]
        }
    for grouping, by_value in rates.items():
        report["breakdown"][grouping] = {
            value: {"documents": sizes[grouping][value],
                    **{o: _interval(series[o][1:], series[o][0]) for o in OUTCOMES}}
            for value, series in sorted(by_value.items())
        }
    return report
