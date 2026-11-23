"""``statements`` CLI group: archive coverage, reconciliation detail, history import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

console = Console(force_terminal=True, legacy_windows=False)
ARCHIVE = Path(__file__).resolve().parents[3] / "data" / "statements"
STATUS_STYLE = {"reconciled": "green", "gaps": "red", "on file": "cyan"}


@click.group("statements")
def statements() -> None:
    """Statement archive and monthly reconciliation."""


@statements.command("coverage")
@click.option("--months", default=12, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def coverage_cmd(months: int, as_json: bool) -> None:
    """Per card and month: statement on file, and whether it reconciles with fetched transactions."""
    from ..db.session import get_engine
    from .archive import coverage

    grid = coverage(get_engine(), months=months)
    if as_json:
        click.echo(json.dumps(grid))
        return
    table = Table(title="Statements by card and month (closing date)")
    table.add_column("card")
    for month in grid["months"]:
        table.add_column(month[2:], justify="center")
    for card, row in grid["cards"].items():
        table.add_row(card, *[f"[{STATUS_STYLE.get(v, 'dim')}]{v or '-'}[/]" for v in row.values()])
    console.print(table)
    console.print("reconciled: every row matches a fetched transaction and nothing fetched is missing; "
                  "gaps: see `statements show <id>`; on file: card not fetched")


@statements.command("list")
def list_cmd() -> None:
    """Archived statements."""
    from sqlalchemy import text

    from ..db.session import get_engine
    from .archive import ensure_schema

    engine = get_engine()
    ensure_schema(engine)
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT id, card, period_start, period_end, rows, charges, original_name FROM statements "
            "ORDER BY card, period_end")).all()
    table = Table(title=f"{len(rows)} archived statements")
    for column in ("id", "card", "period", "rows", "charges", "file"):
        table.add_column(column)
    for r in rows:
        table.add_row(str(r.id), r.card or "", f"{r.period_start} to {r.period_end}", str(r.rows),
                      f"${r.charges:,.2f}", r.original_name)
    console.print(table)


@statements.command("show")
@click.argument("statement_id", type=int)
def show_cmd(statement_id: int) -> None:
    """Reconciliation of one statement: matched rows, rows the fetch missed, fetched rows not on it."""
    from ..db.session import get_engine
    from .archive import statement_report

    report = statement_report(get_engine(), statement_id)
    s = report["statement"]
    console.print(f"{s['card']} · {s['period_start']} to {s['period_end']} · {report['rows']} rows, "
                  f"{report['matched']} matched to recorded expenses")
    if not report["fetched_card"]:
        console.print("This card is not fetched; the statement is the record.")
        return
    console.print("[green]Reconciled[/green]" if report["reconciled"] else "[red]Not reconciled[/red]")
    for row in report["missing_from_fetch"]:
        console.print(f"  on the statement, not fetched: {row['row_date']} {row['description']} ${row['amount']:.2f}",
                      markup=False)
    for row in report["missing_from_statement"]:
        console.print(f"  fetched, not on the statement: {row['date']} {row['merchant']} ${row['amount']:.2f}",
                      markup=False)


@statements.command("import-history")
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--card", default=None, help="Card name when the files do not say.")
def import_history(folder: Path, card: Optional[str]) -> None:
    """Archive statements imported earlier and link their rows to the expenses they produced.

    Creates no expenses: rows with no recorded expense are listed for a look.
    """
    from ..db.session import get_engine
    from ..ingest import read_expenses_csv, read_expenses_excel, read_expenses_pdf
    from ..ingest.csv_reader import infer_statement_closing_date
    from .archive import Row, archive_statement

    engine = get_engine()
    readers = {".csv": read_expenses_csv, ".xlsx": read_expenses_excel, ".xls": read_expenses_excel,
               ".pdf": read_expenses_pdf}
    totals = {"files": 0, "new": 0, "rows": 0, "matched": 0}
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in readers):
        try:
            expenses = list(readers[path.suffix.lower()](path, None, card_name=card))
        except Exception as exc:  # an unreadable file is reported, not fatal
            console.print(f"[yellow]{path.name}: not read ({exc})[/yellow]")
            continue
        if not expenses:
            continue
        totals["files"] += 1
        rows = [Row(e.date, e.original_input_name or e.merchant or "", e.amount) for e in expenses]
        closing = infer_statement_closing_date(path)
        result = archive_statement(engine, path, next((e.card for e in expenses if e.card), card), rows, ARCHIVE,
                                   closing=closing.date() if closing else None)
        if result.already_archived:
            continue
        totals["new"] += 1
        totals["rows"] += len(rows)
        matched = sum(1 for m in result.matches if m.expense_id or not m.spending)
        totals["matched"] += matched
        if matched < len(rows):
            console.print(f"{path.name}: {len(rows) - matched} of {len(rows)} rows have no recorded expense "
                          f"(statement {result.statement_id})", markup=False)
    console.print(f"{totals['files']} statements read, {totals['new']} archived now; "
                  f"{totals['matched']} of {totals['rows']} rows linked to recorded expenses")
