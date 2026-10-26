"""``scans`` CLI group: cut, name, and file FastFoto scan batches."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from .conventions import SELF_KEY, learn_layout
from .pipeline import KINDS, apply_manifest, plan_batch, undo_apply, write_manifest

console = Console(force_terminal=True, legacy_windows=False)
REPO_ROOT = Path(__file__).resolve().parents[3]

onedrive_option = click.option(
    "--onedrive", type=click.Path(file_okay=False, path_type=Path), default=None,
    help="OneDrive root (defaults to %OneDrive%).",
)


@click.group("scans")
def scans() -> None:
    """Cut, name, and file scanned document batches into OneDrive."""


@scans.command("conventions")
@onedrive_option
@click.option("--json", "as_json", is_flag=True, help="Print categories, people, cars, and kinds as JSON.")
def conventions(onedrive: Optional[Path], as_json: bool) -> None:
    """Show the filing layout learned from OneDrive."""
    layout = learn_layout(onedrive)
    if as_json:
        import json

        click.echo(json.dumps({
            "onedrive": str(layout.onedrive),
            "categories": layout.receipt_categories,
            "people": [{"key": p.key, "name": p.display_name} for p in layout.people.values()],
            "cars": sorted(layout.cars),
            "kinds": sorted(KINDS),
        }))
        return
    console.print(f"[bold]OneDrive:[/bold] {layout.onedrive}")

    counts = Counter(
        category for per_merchant in layout.merchant_categories.values()
        for category, n in per_merchant.items() for _ in range(n)
    )
    table = Table(title="Receipt categories")
    table.add_column("Category", style="cyan")
    table.add_column("Named receipts", justify="right")
    for category in layout.receipt_categories:
        table.add_row(category, str(counts.get(category, 0)))
    console.print(table)
    console.print(f"Known merchants: {len(layout.merchant_categories)}")

    people = Table(title="People")
    people.add_column("Key", style="cyan")
    people.add_column("Receipts folder")
    for person in layout.people.values():
        status = "" if person.receipts_root_exists else " [dim](will be created)[/dim]"
        people.add_row(person.key, str(person.receipts_root.relative_to(layout.onedrive)) + status)
    console.print(people)
    console.print(f"Cars: {', '.join(layout.cars) or '-'}")
    console.print(f"Manual brands: {', '.join(sorted(layout.manual_brands)) or '-'}")


@scans.command("plan")
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@onedrive_option
@click.option("--person", default=SELF_KEY, show_default=True,
              help="Whose documents these are: 'self' or a people folder / first name (e.g. sam).")
@click.option("--kind", type=click.Choice(sorted(KINDS)), default=None,
              help="Force a document kind for the whole batch (e.g. manual).")
@click.option("--duplex", is_flag=True, help="Pages alternate front/back (stack PDF or ScanSmart one-page PDFs).")
@click.option("--keep-pdfs-whole", is_flag=True, help="Treat each input PDF as one document.")
@click.option("--separator-sheets", is_flag=True,
              help="A blank sheet between documents always starts a new document.")
@click.option("--one-per-page", is_flag=True, help="Do not join pages into multi-page documents.")
@click.option("--ocr", "ocr_choice", type=click.Choice(["auto", "tesseract", "rapidocr", "none"]),
              default="auto", show_default=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Manifest path (defaults to data/scans/<timestamp>/manifest.csv).")
def plan(input_dir, onedrive, person, kind, duplex, keep_pdfs_whole, separator_sheets, one_per_page,
         ocr_choice, out) -> None:
    """Cut INPUT_DIR into documents and write a reviewable manifest. Writes nothing to OneDrive."""
    from ..ocr import get_ocr_engine
    from .store_directory import attach_phone_book

    layout = attach_phone_book(learn_layout(onedrive))
    if input_dir.resolve().is_relative_to(layout.documents.resolve()):
        console.print(
            "[yellow]The scan folder is inside OneDrive/Documents. Raw scans there stay in the filed "
            "tree after apply; point the scanner at a separate inbox folder instead.[/yellow]"
        )
    ocr = get_ocr_engine(ocr_choice)
    if ocr is None and ocr_choice != "none":
        console.print("[yellow]No OCR engine found; only PDF text layers and sidecar .txt files will be read.[/yellow]")
    page_texts: dict[str, str] = {}
    rows = plan_batch(input_dir, layout, person=person, kind=kind, duplex=duplex,
                      keep_pdfs_whole=keep_pdfs_whole, ocr=ocr, separator_sheets=separator_sheets,
                      group_pages=not one_per_page, page_texts=page_texts)
    out = out or REPO_ROOT / "data" / "scans" / f"{datetime.now():%Y%m%d_%H%M%S}" / "manifest.csv"
    write_manifest(rows, out, page_texts=page_texts)

    documents = [r for r in rows if not r["merge_into"]]
    parts = Counter(r["merge_into"] for r in rows if r["merge_into"])
    table = Table(title=f"{len(documents)} documents from {len(rows)} scanned pages")
    for column in ("item_id", "action", "pages", "kind", "person", "destination", "filename"):
        table.add_column(column)
    for row in documents:
        style = "green" if row["action"] == "file" else "yellow"
        joined = [r["item_id"] for r in rows if r["merge_into"] == row["item_id"]]
        span = f"{row['item_id']}-{joined[-1]}" if joined else row["item_id"]
        table.add_row(row["item_id"], row["action"], span, row["kind"], row["person"],
                      row["destination"], row["filename"], style=style)
    console.print(table)
    ready = sum(r["action"] == "file" for r in documents)
    console.print(f"[green]{ready} ready to file[/green], [yellow]{len(documents) - ready} need review[/yellow]"
                  f"; {sum(parts.values())} pages joined into {len(parts)} multi-page documents")
    console.print(f"Manifest: {out}")
    console.print("Edit it (set action=file/review/skip, fix fields; set merge_into to join pages, "
                  "clear it to split them), then run:")
    console.print(f'  python -m backend.src.main scans apply "{out}"')


@scans.command("apply")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@onedrive_option
@click.option("--dry-run", is_flag=True, help="Show what would be written without writing.")
@click.option("--no-index", is_flag=True, help="Do not record the filed documents' text in the database.")
@click.option("--json", "as_json", is_flag=True, help="Print the result as JSON.")
def apply(manifest, onedrive, dry_run, no_index, as_json) -> None:
    """File every action=file row of MANIFEST into OneDrive (copies; never overwrites)."""
    import json

    from ..db.session import get_engine

    layout = learn_layout(onedrive)
    try:
        result = apply_manifest(manifest, layout, dry_run=dry_run,
                                index_engine=None if no_index else get_engine())
    except ValueError as exc:
        if as_json:
            click.echo(json.dumps({"error": str(exc)}))
            return
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    if as_json:
        click.echo(json.dumps({
            "dry_run": dry_run,
            "filed": [{"item_id": p.item_id, "target": str(p.target.relative_to(layout.onedrive)).replace("\\", "/"),
                       "pages": len(p.pages)} for p in result.filed],
            "review": result.review, "skipped": result.skipped, "merged": result.merged,
            "index_errors": result.index_errors,
            "log": str(result.log_path) if result.log_path else None,
        }))
        return
    verb = "Would file" if dry_run else "Filed"
    for planned in result.filed:
        console.print(f"  {verb} {planned.item_id} -> {planned.target.relative_to(layout.onedrive)}")
    console.print(
        f"{verb} {len(result.filed)}; review {len(result.review)}; "
        f"skipped {len(result.skipped)}; merged {len(result.merged)}"
    )
    if result.review:
        console.print(f"[yellow]Still in review: {', '.join(result.review)}[/yellow]")
    for error in result.index_errors:
        console.print(f"[yellow]Filed, but not indexed (run scans index-text later): {error}[/yellow]")
    if result.log_path:
        console.print(f"Undo with: python -m backend.src.main scans undo \"{result.log_path}\"")


@scans.command("evaluate")
@onedrive_option
@click.option("--split", type=click.Choice(["dev", "test", "all"]), default="dev", show_default=True,
              help="Tune on dev; look at test only to report.")
@click.option("--exclude", "exclude_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="JSON list of document paths to leave out (e.g. seen while tuning).")
@click.option("--show-misses", default=15, show_default=True)
@click.option("--json-out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write all metrics, the category confusion matrix, and misses as JSON.")
@click.option("--phone-book/--no-phone-book", default=True, show_default=True,
              help="Recognise stores by phone number (each document left out of its own lookup).")
def evaluate_cmd(onedrive, split, exclude_file, show_misses, json_out, phone_book) -> None:
    """Score naming and filing against every labelled, indexed document (reads only the database)."""
    import json

    from ..db.session import get_engine
    from .naming_eval import evaluate_naming, wilson
    from .segmentation_eval import load_census

    layout = learn_layout(onedrive)
    if phone_book:
        from .store_directory import attach_phone_book

        attach_phone_book(layout)
    docs, coverage = load_census(get_engine(), layout)
    console.print(f"Census: {coverage.indexed} of {coverage.files} filed PDFs indexed "
                  f"({coverage.no_text} with no readable text)")
    excluded = set(json.loads(exclude_file.read_text(encoding="utf-8"))) if exclude_file else set()
    docs = [d for d in docs if (split == "all" or d.split == split) and d.key not in excluded]
    report = evaluate_naming(layout, docs)

    table = Table(title=f"{split}: {report.documents} documents; accuracy [95% Wilson CI]")
    for column in ("field", "n", "accuracy", "wrong", "missing"):
        table.add_column(column, justify="left" if column == "field" else "right")
    for name, score in report.fields.items():
        s = score.summary()
        p, lo, hi = s["accuracy"]
        table.add_row(name, str(s["n"]), f"{p:.3f} [{lo:.3f}, {hi:.3f}]", f"{s['wrong']:.3f}",
                      f"{s['missing']:.3f}")
    console.print(table)

    p, lo, hi = wilson(report.auto_filed, report.documents)
    q, qlo, qhi = wilson(report.auto_filed_exact, report.auto_filed)
    console.print(f"Auto-file coverage {p:.3f} [{lo:.3f}, {hi:.3f}] ({report.auto_filed} documents); "
                  f"exact filename + folder among them {q:.3f} [{qlo:.3f}, {qhi:.3f}]")

    per_class, macro_f1 = report.category_table()
    table = Table(title=f"category (macro-F1 {macro_f1:.3f})")
    for column in ("category", "support", "precision", "recall", "f1"):
        table.add_column(column, justify="left" if column == "category" else "right")
    for name, row in sorted(per_class.items(), key=lambda kv: -kv[1]["support"]):
        table.add_row(name, str(row["support"]), f"{row['precision']:.3f}", f"{row['recall']:.3f}",
                      f"{row['f1']:.3f}")
    console.print(table)
    for miss in report.misses[:show_misses]:
        console.print(f"  {miss['action']:<6} {miss['file']}  ->  {miss['predicted']}", markup=False,
                      highlight=False)
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps({
            "coverage": coverage.__dict__, "documents": report.documents,
            "fields": {k: v.summary() for k, v in report.fields.items()},
            "auto_filed": report.auto_filed, "auto_filed_exact": report.auto_filed_exact,
            "category": per_class, "category_macro_f1": macro_f1,
            "category_confusion": [[t, p_, n] for (t, p_), n in sorted(report.category_confusion.items())],
            "by_folder": {k: dict(v) for k, v in report.by_folder.items()},
            "misses": report.misses,
        }, indent=2), encoding="utf-8")


@scans.command("evaluate-segmentation")
@onedrive_option
@click.option("--split", type=click.Choice(["dev", "test", "all"]), default="dev", show_default=True,
              help="Tune on dev; look at test only to report.")
@click.option("--rounds", default=200, show_default=True, help="Bootstrap rounds.")
@click.option("--exclude", "exclude_file", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              default=None, help="JSON list of document paths to leave out (e.g. seen while tuning).")
@click.option("--order", type=click.Choice(["shuffled", "date"]), default="shuffled", show_default=True,
              help="Stack order: shuffled, or by date like a real scan session (harder).")
@click.option("--phone-book/--no-phone-book", default=True, show_default=True,
              help="Recognise stores by phone number (each document left out of its own lookup).")
@click.option("--json-out", type=click.Path(dir_okay=False, path_type=Path), default=None)
def evaluate_segmentation_cmd(onedrive, split, rounds, exclude_file, order, phone_book, json_out) -> None:
    """Segmentation metrics with bootstrap CIs over every indexed document (reads only the database).

    Run ``scans index-text`` first; coverage of the files on disk is reported.
    """
    import json

    from ..db.session import get_engine
    from .segmentation_eval import OUTCOMES, doc_units, evaluate_segmentation, load_census

    layout = learn_layout(onedrive)
    if phone_book:
        from .store_directory import attach_phone_book

        attach_phone_book(layout)
    docs, coverage = load_census(get_engine(), layout)
    console.print(f"Census: {coverage.indexed} of {coverage.files} filed PDFs indexed "
                  f"({coverage.no_text} with no readable text)")
    excluded = set(json.loads(exclude_file.read_text(encoding="utf-8"))) if exclude_file else set()
    docs = [d for d in docs if (split == "all" or d.split == split) and d.key not in excluded]
    report = evaluate_segmentation(docs, doc_units(docs, layout), rounds=rounds, order=order)

    def fmt(value, lo, hi, count=False):
        return f"{value:.0f} [{lo:.0f}, {hi:.0f}]" if count else f"{value:.3f} [{lo:.3f}, {hi:.3f}]"

    table = Table(title=f"{split}: {report['documents']} documents ({report['multi_page']} multi-page), "
                        f"{report['pages']} pages; point estimate [95% bootstrap CI], {rounds} rounds; "
                        f"Pk k={report['k']}")
    table.add_column("metric")
    for system in report["systems"]:
        table.add_column(system, justify="right")
    for metric in report["systems"]["rules"]:
        count = metric in {"boundary_tp", "boundary_fp", "boundary_fn", "boundary_tn", "wrong_joins"}
        table.add_row(metric, *(fmt(*report["systems"][s][metric], count=count) for s in report["systems"]))
    console.print(table)

    for grouping, by_value in report["breakdown"].items():
        table = Table(title=f"rules, per document, by {grouping} (share of documents)")
        table.add_column(grouping)
        table.add_column("documents", justify="right")
        for outcome in OUTCOMES:
            table.add_column(outcome, justify="right")
        for value, row in by_value.items():
            table.add_row(value, str(row["documents"]), *(fmt(*row[o]) for o in OUTCOMES))
        console.print(table)
    if json_out:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps({"coverage": coverage.__dict__, **report}, indent=2), encoding="utf-8")


@scans.command("index-text")
@onedrive_option
@click.option("--path", "paths", multiple=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder to index (repeatable). Defaults to receipts, cars, manuals, instructions, people.")
@click.option("--include-online-only", is_flag=True,
              help="Also read online-only OneDrive files. This downloads them.")
@click.option("--ocr", "ocr_choice", type=click.Choice(["auto", "tesseract", "rapidocr", "none"]),
              default="auto", show_default=True)
@click.option("--import-cache", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="JSON of page texts (path -> pages) to load instead of reading those files again.")
def index_text(onedrive, paths, include_online_only, ocr_choice, import_cache) -> None:
    """Read every filed document once and store its text in the database (searchable).

    Run it again any time: only new or changed files are read, and renamed or
    moved files keep their text.
    """
    import json

    from ..db.session import get_engine
    from ..ocr import get_ocr_engine
    from .text_index import index_documents

    layout = learn_layout(onedrive)
    engine = get_engine()
    console.print(f"Database: {engine.url.database}")
    known = json.loads(import_cache.read_text(encoding="utf-8")) if import_cache else None
    report = index_documents(
        engine, layout, get_ocr_engine(ocr_choice), roots=list(paths) or None,
        include_online_only=include_online_only, known_text=known,
        progress=lambda i, n: i % 100 == 0 and console.print(f"  {i}/{n} files"),
    )
    console.print(
        f"Read {len(report.indexed)}; reused stored text {len(report.reused)}; moved/renamed {report.moved}; "
        f"unchanged {report.unchanged}; online-only skipped {report.skipped_online_only}; "
        f"removed {report.removed}; errors {len(report.errors)}"
    )
    for error in report.errors[:10]:
        console.print(f"  [red]{error}[/red]")


@scans.command("search")
@click.argument("query")
@click.option("--limit", default=20, show_default=True)
def search_cmd(query, limit) -> None:
    """Full-text search over indexed documents, e.g. "brake pads" or 'civic AND invoice'."""
    from ..db.session import get_engine
    from .text_index import search

    hits = search(get_engine(), query, limit=limit)
    for hit in hits:
        console.print(f"[cyan]{hit.path}[/cyan] p{hit.page}  {hit.snippet}", markup=False, highlight=False)
    console.print(f"{len(hits)} hits")


@scans.command("export-text")
@click.argument("out", type=click.Path(dir_okay=False, path_type=Path),
                default=REPO_ROOT / "data" / "scans" / "document_text.xlsx")
def export_text(out) -> None:
    """Write indexed documents and their page text to an Excel workbook."""
    from ..db.session import get_engine
    from .text_index import export_xlsx

    n_docs, n_pages = export_xlsx(get_engine(), out)
    console.print(f"Wrote {n_docs} documents and {n_pages} pages to {out}")


EDITABLE_MANIFEST_FIELDS = {"action", "kind", "person", "category", "car", "date", "time", "merchant",
                            "filename_override", "merge_into"}


@scans.command("manifest-json")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@onedrive_option
def manifest_json(manifest, onedrive) -> None:
    """Print a manifest as JSON, with the names and folders apply would use (for the app)."""
    import json

    from .pipeline import read_manifest, resolve_plan

    rows = read_manifest(manifest)
    planned, problems = {}, None
    try:
        result = resolve_plan(rows, learn_layout(onedrive))
        layout_root = learn_layout(onedrive).onedrive
        planned = {p.item_id: str(p.target.relative_to(layout_root)).replace("\\", "/") for p in result.filed}
    except ValueError as exc:
        problems = str(exc)
    click.echo(json.dumps({"rows": rows, "targets": planned, "problems": problems}))


@scans.command("describe")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("groups_json")
@onedrive_option
def describe_cmd(manifest, groups_json, onedrive) -> None:
    """Name/date/folder for re-cut documents: GROUPS_JSON is [["0002", "0003"], ...] (for the app)."""
    import json

    from .pipeline import describe_documents
    from .store_directory import attach_phone_book

    layout = attach_phone_book(learn_layout(onedrive))
    click.echo(json.dumps(describe_documents(manifest, layout, json.loads(groups_json))))


@scans.command("manifest-set")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("edits_json")
def manifest_set(manifest, edits_json) -> None:
    """Apply edits [{"item_id": .., "field": value, ...}] to a manifest (editable fields only)."""
    import json

    from .pipeline import read_manifest, texts_path, write_manifest

    rows = read_manifest(manifest)
    by_id = {r["item_id"]: r for r in rows}
    for edit in json.loads(edits_json):
        row = by_id.get(str(edit.get("item_id")))
        if row is None:
            raise click.ClickException(f"no item {edit.get('item_id')}")
        for field, value in edit.items():
            if field == "item_id":
                continue
            if field not in EDITABLE_MANIFEST_FIELDS:
                raise click.ClickException(f"{field} cannot be edited")
            row[field] = str(value or "").strip()
    sidecar = texts_path(manifest)
    write_manifest(rows, manifest, page_texts=json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else None)
    click.echo(json.dumps({"saved": len(rows)}))


@scans.command("match-receipts")
@click.option("--check-only", is_flag=True, help="Only score against expenses already linked by filename.")
@click.option("--confirm-above", type=float, default=None,
              help="Confirm links scoring at least this directly (6 = measured 99% precision).")
@click.option("--json", "as_json", is_flag=True)
def match_receipts_cmd(check_only, confirm_above, as_json) -> None:
    """Link scanned receipts to expenses (suggestions; strong ones confirmed with --confirm-above)."""
    import json

    from ..db.session import get_engine
    from .receipt_matching import evaluate_against_links, match_receipts

    engine = get_engine()
    check = evaluate_against_links(engine)
    if not as_json:
        console.print(f"On {check['linked']} expenses you linked by filename: {check['correct']} right, "
                      f"{check['wrong']} different, {check['no_match']} not found")
        for miss in check["misses"]:
            console.print(f"  {miss}", markup=False)
    if check_only:
        return
    report = match_receipts(engine, confirm_above=confirm_above)
    if as_json:
        click.echo(json.dumps({"confirmed": report.confirmed, "suggested": report.suggested,
                               "linked_by_filename": report.linked_by_filename,
                               "replaced_receipt_expenses": report.replaced_receipt_expenses}))
        return
    console.print(f"Linked by filename {report.linked_by_filename}; confirmed {report.confirmed}; "
                  f"suggested {report.suggested} ({dict(report.by_method)}); "
                  f"earlier decisions kept {report.kept_decisions}; "
                  f"receipt expenses replaced by statement rows {report.replaced_receipt_expenses}")


@scans.command("receipts-to-expenses")
@click.option("--apply", "do_apply", is_flag=True, help="Create the staged expenses (default: show the plan).")
@click.option("--show", default=10, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def receipts_to_expenses(do_apply, show, as_json) -> None:
    """Make staged expenses for receipts that no statement covers (approve them in the Validation Queue)."""
    import json

    from ..db.session import get_engine
    from .receipt_expenses import create_receipt_expenses

    report = create_receipt_expenses(get_engine(), dry_run=not do_apply)
    if as_json:
        click.echo(json.dumps({"applied": do_apply, "created": len(report.created),
                               "skipped": dict(report.skipped)}))
        return
    verb = "Created" if do_apply else "Would create"
    total = sum(p.amount for p in report.created)
    console.print(f"{verb} {len(report.created)} staged expenses (${total:,.2f}) from receipts")
    by_year = Counter(p.date.year for p in report.created)
    console.print("  by year: " + ", ".join(f"{y}: {n}" for y, n in sorted(by_year.items())))
    for plan in report.created[:show]:
        console.print(f"  {plan.date} {plan.merchant} ${plan.amount:.2f} {plan.spender} {plan.card or '-'} "
                      f"[{plan.type} / {plan.subtype or '-'}]", markup=False)
    console.print("Not made into expenses:")
    for reason, n in report.skipped.most_common():
        console.print(f"  {n:4d}  {reason}  e.g. {', '.join(report.examples[reason][:3])}", markup=False)


@scans.command("inbox-tick")
@onedrive_option
@click.option("--inbox", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Scan inbox (defaults to OneDrive/Scans/Inbox).")
@click.option("--settle-seconds", default=90, show_default=True,
              help="Wait until nothing in the inbox changed for this long.")
@click.option("--json", "as_json", is_flag=True)
def inbox_tick_cmd(onedrive, inbox, settle_seconds, as_json) -> None:
    """Turn scans waiting in the inbox into review batches (run by a scheduled task)."""
    import json

    from ..ocr import get_ocr_engine
    from .conventions import default_onedrive_root
    from .inbox import inbox_tick, lower_priority, pending_scans

    lower_priority()
    inbox = inbox or Path(onedrive or default_onedrive_root()) / "Scans" / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    if not pending_scans(inbox):  # the usual case every few minutes: stay cheap
        if as_json:
            click.echo(json.dumps({"batches": [], "waiting": None, "errors": []}))
        else:
            console.print(f"Nothing in {inbox}")
        return
    from .store_directory import attach_phone_book

    layout = attach_phone_book(learn_layout(onedrive))
    result = inbox_tick(layout, REPO_ROOT / "data" / "scans", get_ocr_engine("auto"), inbox=inbox,
                        settle_seconds=settle_seconds)
    if as_json:
        click.echo(json.dumps({"batches": result.batches, "waiting": result.waiting, "errors": result.errors}))
        return
    for batch in result.batches:
        console.print(f"New batch to review: {batch}")
    if result.waiting:
        console.print(f"Waiting: {result.waiting}")
    for error in result.errors:
        console.print(f"[red]{error}[/red]", markup=True)
    if not (result.batches or result.waiting or result.errors):
        console.print(f"Nothing in {inbox}")


@scans.command("render-pages")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def render_pages_cmd(manifest) -> None:
    """Page thumbnails and previews of a batch, for the review dashboard."""
    from .inbox import render_pages

    console.print(f"{len(render_pages(manifest))} pages rendered")


@scans.command("replan")
@click.argument("batch_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@onedrive_option
def replan(batch_dir, onedrive) -> None:
    """Cut an inbox batch again from its originals (after a failed run)."""
    import json

    from ..ocr import get_ocr_engine
    from .inbox import lower_priority, plan_inbox_batch

    lower_priority()
    from .store_directory import attach_phone_book

    info = json.loads((batch_dir / "batch.json").read_text(encoding="utf-8"))
    layout = attach_phone_book(learn_layout(onedrive))
    manifest = plan_inbox_batch(layout, Path(info["originals"]), batch_dir, info["person"], get_ocr_engine("auto"))
    console.print(f"Manifest: {manifest}")


@scans.command("settle")
@click.option("--json", "as_json", is_flag=True)
def settle(as_json) -> None:
    """After filing: link new receipts to statement rows, and make expenses for the rest."""
    import json

    from ..db.session import get_engine
    from .inbox import lower_priority
    from .receipt_expenses import create_receipt_expenses
    from .receipt_matching import CONFIRM_SCORE, match_receipts

    lower_priority()
    engine = get_engine()
    matched = match_receipts(engine, confirm_above=CONFIRM_SCORE)
    created = create_receipt_expenses(engine, dry_run=False)
    summary = {"confirmed": matched.confirmed, "suggested": matched.suggested,
               "merged": matched.replaced_receipt_expenses, "receipt_expenses": len(created.created)}
    if as_json:
        click.echo(json.dumps(summary))
        return
    console.print(f"Linked {summary['confirmed']} receipts to statement rows ({summary['suggested']} suggestions); "
                  f"merged {summary['merged']} receipt expenses; made {summary['receipt_expenses']} new expenses")


@scans.command("receipt-coverage")
@click.option("--json", "as_json", is_flag=True)
def receipt_coverage(as_json) -> None:
    """How every filed receipt maps to an expense (or why it does not)."""
    import json

    from ..db.session import get_engine
    from .receipt_coverage import coverage

    result = coverage(get_engine())
    if as_json:
        click.echo(json.dumps(result))
        return
    table = Table(title=f"{result['total']} receipts and car documents")
    table.add_column("status")
    table.add_column("documents", justify="right")
    for status, n in result["by_status"].items():
        table.add_row(status, str(n))
    console.print(table)


@scans.command("audit-labels")
@onedrive_option
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path),
              default=REPO_ROOT / "data" / "scans" / "label_audit.xlsx", show_default=True)
def audit_labels(onedrive, out) -> None:
    """Find names and folders that are probably wrong (from labels and stored text). Renames nothing."""
    from ..db.session import get_engine
    from .label_audit import (
        duplicate_findings,
        export_audit_xlsx,
        label_findings,
        save_findings,
        summarize,
        text_findings,
        vote_on_variants,
    )
    from .segmentation_eval import load_census

    layout = learn_layout(onedrive)
    engine = get_engine()
    docs, coverage = load_census(engine, layout)
    console.print(f"Text available for {coverage.indexed} of {coverage.files} filed PDFs")
    findings = label_findings(layout) + duplicate_findings(engine) + text_findings(layout, docs)
    vote_on_variants(findings, {d.key: d.pages for d in docs})
    added, updated = save_findings(engine, findings)
    for check, n in sorted(summarize(findings).items()):
        console.print(f"  {check:<20} {n}")
    console.print(f"{added} new findings, {updated} updated (verdicts and decisions kept)")
    console.print(f"Workbook: {out} ({export_audit_xlsx(engine, out)} findings)")


@scans.command("audit-sample")
@onedrive_option
@click.option("--check", "checks", multiple=True, help="Checks to sample (default: the text_* checks).")
@click.option("--per-check", default=20, show_default=True)
@click.option("--seed", default=7, show_default=True)
@click.option("--images", type=click.Path(file_okay=False, path_type=Path),
              default=REPO_ROOT / "data" / "scans" / "audit_pages", show_default=True)
def audit_sample(onedrive, checks, per_check, seed, images) -> None:
    """Pick a random sample of unreviewed findings and render their pages for review."""
    from ..db.session import get_engine
    from .label_audit import render_pages, review_sample

    layout = learn_layout(onedrive)
    rows = review_sample(get_engine(), checks or ("text_date", "text_time", "text_merchant"), per_check, seed)
    for row in rows:
        pages = render_pages(layout, row.path, images, f"{row.id:05d}")
        console.print(f"#{row.id} {row.check}  {row.path}\n   label {row.label} | {row.evidence}\n   "
                      + ", ".join(str(p) for p in pages), markup=False, highlight=False)


@scans.command("audit-verdict")
@click.argument("finding_id", type=int)
@click.argument("verdict", type=click.Choice(["label_wrong", "text_wrong", "both_wrong", "unclear"]))
@click.option("--note", default="")
@click.option("--by", "reviewer", default="claude", show_default=True)
def audit_verdict(finding_id, verdict, note, reviewer) -> None:
    """Record what the page image shows for a finding."""
    from ..db.session import get_engine
    from .label_audit import record_verdict

    record_verdict(get_engine(), finding_id, verdict, note, reviewer)
    console.print(f"#{finding_id}: {verdict}")


@scans.command("audit-decide")
@click.argument("finding_ids", type=int, nargs=-1, required=True)
@click.argument("decision", type=click.Choice(["fix", "keep", "later"]))
@click.option("--value", default=None, help="Corrected value to use for the fix (date, time, name, ...).")
@click.option("--note", default=None)
def audit_decide(finding_ids, decision, value, note) -> None:
    """Decide what to do with findings: fix (rename on fix-labels), keep, or later."""
    from sqlalchemy.orm import Session

    from ..db.models import LabelFinding
    from ..db.session import get_engine

    with Session(get_engine()) as session:
        for finding_id in finding_ids:
            row = session.get(LabelFinding, finding_id)
            if row is None:
                raise click.ClickException(f"no finding {finding_id}")
            row.user_decision = decision
            if value is not None:
                row.suggestion = value
            if note:
                row.reviewer_note = note if not row.reviewer_note else f"{row.reviewer_note} | {note}"
        session.commit()
    console.print(f"{len(finding_ids)} findings: {decision}")


@scans.command("fix-labels")
@onedrive_option
@click.option("--apply", "do_apply", is_flag=True, help="Rename/move the files (default: show the plan).")
@click.option("--json", "as_json", is_flag=True, help="Print the plan or result as JSON.")
def fix_labels(onedrive, do_apply, as_json) -> None:
    """Rename or move files for findings decided as 'fix'. Never overwrites; writes an undo log."""
    import json

    from ..db.session import get_engine
    from .label_fixes import apply_fixes

    layout = learn_layout(onedrive)
    engine = get_engine()
    # Undo logs live beside the database so they survive with it.
    log_dir = Path(engine.url.database).resolve().parent / "label_fixes"
    result = apply_fixes(engine, layout, log_dir, dry_run=not do_apply)
    if as_json:
        click.echo(json.dumps({
            "applied": do_apply,
            "renames": [{"from": p.source, "to": p.target, "checks": p.checks} for p in result.applied],
            "skipped": [{"from": p.source, "problem": p.problem} for p in result.skipped],
            "log": str(result.log_path) if result.log_path else None,
        }))
        return
    verb = "Renamed" if do_apply else "Would rename"
    for plan in result.applied:
        console.print(f"  {plan.source}\n    -> {plan.target}  ({', '.join(plan.checks)})", markup=False,
                      highlight=False)
    for plan in result.skipped:
        console.print(f"  [yellow]skipped[/yellow] {plan.source}: {plan.problem}", highlight=False)
    console.print(f"{verb} {len(result.applied)}; skipped {len(result.skipped)}")
    if result.log_path:
        console.print(f'Undo with: python -m backend.src.main scans undo-label-fixes "{result.log_path}"')


@scans.command("undo-label-fixes")
@click.argument("log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@onedrive_option
def undo_label_fixes(log, onedrive) -> None:
    """Move files renamed by fix-labels back to their old names."""
    from ..db.session import get_engine
    from .label_fixes import undo_fixes

    undone, problems = undo_fixes(get_engine(), learn_layout(onedrive), log)
    for problem in problems:
        console.print(f"  [yellow]{problem}[/yellow]")
    console.print(f"Moved back {len(undone)}; problems {len(problems)}")


@scans.command("audit-report")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path),
              default=REPO_ROOT / "data" / "scans" / "label_audit.xlsx", show_default=True)
def audit_report_cmd(out) -> None:
    """Estimated share of wrong labels per check, from the reviewed sample (95% Wilson CI)."""
    from ..db.session import get_engine
    from .label_audit import audit_report, export_audit_xlsx

    engine = get_engine()
    table = Table(title="label audit")
    for column in ("check", "findings", "reviewed", "label wrong", "est. wrong labels", "text wrong", "unclear"):
        table.add_column(column, justify="left" if column == "check" else "right")
    for check, r in audit_report(engine).items():
        p, lo, hi = r["label_wrong_rate"]
        est = r["estimated_wrong_labels"]
        table.add_row(check, str(r["findings"]), str(r["reviewed"]),
                      f"{p:.2f} [{lo:.2f}, {hi:.2f}]" if r["reviewed"] else "-",
                      f"{est[0]} [{est[1]}, {est[2]}]" if est else "-", str(r["text_wrong"]), str(r["unclear"]))
    console.print(table)
    export_audit_xlsx(engine, out)
    console.print(f"Workbook: {out}")


@scans.command("set-dates")
@onedrive_option
@click.option("--path", "paths", multiple=True, type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Folder to restamp (repeatable). Defaults to receipts, cars, and people's receipts.")
@click.option("--apply", "do_apply", is_flag=True, help="Actually change timestamps (default is a dry run).")
@click.option("--skip-online-only", is_flag=True,
              help="Leave OneDrive files that are not downloaded alone (they are not downloaded either way).")
@click.option("--show", default=10, show_default=True, help="How many changes to list.")
def set_dates(onedrive, paths, do_apply, skip_online_only, show) -> None:
    """Set created/modified times of already-filed files to the date in their name."""
    from .filedates import restamp

    layout = learn_layout(onedrive)
    roots = list(paths) or [
        layout.receipts,
        layout.documents / "cars",
        *(p.receipts_root for p in layout.people.values() if p.receipts_root_exists),
    ]
    report = restamp(roots, dry_run=not do_apply, include_online_only=not skip_online_only)
    verb = "Restamped" if do_apply else "Would restamp"
    for path, when in report.changed[:show]:
        console.print(f"  {when:%Y-%m-%d %H:%M}  {path.relative_to(layout.onedrive)}")
    console.print(
        f"{verb} {len(report.changed)}; already correct {report.already_ok}; "
        f"no date in name {report.undated}; online-only skipped {len(report.skipped_online_only)}; "
        f"errors {len(report.errors)}"
    )
    for error in report.errors[:show]:
        console.print(f"  [red]{error}[/red]")
    if not do_apply and report.changed:
        console.print("Run again with --apply to change them.")


@scans.command("undo")
@click.argument("log", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@onedrive_option
def undo(log, onedrive) -> None:
    """Remove files written by an apply run (only if unchanged since)."""
    from ..db.session import get_engine

    removed, kept = undo_apply(log, index_engine=get_engine(), onedrive=learn_layout(onedrive).onedrive)
    for path in removed:
        console.print(f"  removed {path}")
    for note in kept:
        console.print(f"  [yellow]kept {note}[/yellow]")
    console.print(f"Removed {len(removed)}, kept {len(kept)}")
