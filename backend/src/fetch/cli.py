"""``fetch`` CLI group: connect SimpleFIN Bridge, fetch transactions, map accounts."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

console = Console(force_terminal=True, legacy_windows=False)
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA = REPO_ROOT / "data"
# The access URL carries credentials: kept in its own file (data/ is not
# committed), not in the database, which is copied by backups.
ACCESS_FILE = DATA / "secrets" / "simplefin_access_url"
SNAPSHOTS = DATA / "fetch" / "simplefin"


def _access_url() -> str:
    if not ACCESS_FILE.exists():
        raise click.ClickException("Not connected. Run: fetch connect <setup token>")
    return ACCESS_FILE.read_text(encoding="utf-8").strip()


@click.group("fetch")
def fetch() -> None:
    """Fetch card and bank transactions (SimpleFIN Bridge)."""


@fetch.command("connect")
@click.argument("setup_token")
def connect(setup_token: str) -> None:
    """Claim a SimpleFIN setup token (from the Bridge's "New connection" page) and store the access URL."""
    from .simplefin import SimplefinError, claim

    try:
        access_url = claim(setup_token)
    except SimplefinError as exc:
        raise click.ClickException(str(exc)) from exc
    ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCESS_FILE.write_text(access_url, encoding="utf-8")
    console.print("Connected. Fetch with: fetch run")


@fetch.command("run")
@click.option("--since", type=click.DateTime(["%Y-%m-%d"]), default=None,
              help="Fetch from this date (default: 5 days before each account's last transaction, or 90 days).")
@click.option("--no-match", is_flag=True, help="Do not match receipts to the new expenses afterwards.")
@click.option("--json", "as_json", is_flag=True)
def run(since: Optional[datetime], no_match: bool, as_json: bool) -> None:
    """Fetch new transactions; new ones become expenses waiting for approval."""
    from ..categorize import categorize_expenses_batch
    from ..db import init_db
    from ..db.session import get_engine
    from .importer import run_fetch
    from .simplefin import SimplefinError

    init_db()
    engine = get_engine()
    try:
        report = run_fetch(engine, _access_url(), since=since.date() if since else None,
                           snapshot_dir=SNAPSHOTS, categorize=categorize_expenses_batch)
    except SimplefinError as exc:
        raise click.ClickException(str(exc)) from exc
    matched = None
    if report.expenses and not no_match:
        from ..scans.receipt_matching import CONFIRM_SCORE, match_receipts

        matched = match_receipts(engine, confirm_above=CONFIRM_SCORE)
    if as_json:
        click.echo(json.dumps({
            "start": str(report.start), "end": str(report.end), "accounts": report.accounts,
            "new_accounts": report.new_accounts, "new": report.new, "expenses": report.expenses,
            "linked": report.linked, "skipped": report.skipped, "errors": report.errors,
            "receipts_confirmed": matched.confirmed if matched else 0,
        }))
        return
    console.print(f"Fetched {report.start} to {report.end}: {report.accounts} accounts, {report.new} new "
                  f"transactions -> {report.expenses} new expenses, {report.linked} linked to statement rows"
                  + (f", skipped {report.skipped}" if report.skipped else ""))
    for card in report.new_accounts:
        console.print(f"[yellow]New account '{card}': set its card name and person with "
                      f"fetch set-account (see fetch accounts)[/yellow]")
    for error in report.errors:
        console.print(f"[red]SimpleFIN: {error}[/red]")
    if matched:
        console.print(f"Receipts matched: {matched.confirmed} confirmed, {matched.suggested} suggested")


@fetch.command("accounts")
@click.option("--json", "as_json", is_flag=True)
def accounts_cmd(as_json: bool) -> None:
    """Fetched accounts, their card names, and how fresh they are."""
    from ..db.session import get_engine
    from .importer import accounts

    rows = accounts(get_engine())
    if as_json:
        click.echo(json.dumps(rows, default=str))
        return
    table = Table(title="Fetched accounts")
    for column in ("id", "account", "card name", "person", "kind", "last transaction", "fetched", "error"):
        table.add_column(column)
    today = date.today()
    for r in rows:
        last = r["last_transaction_date"]
        stale = last and (today - date.fromisoformat(last)).days > 10
        table.add_row(r["account_id"], f"{r['connection'] or ''} {r['name']}".strip(), r["card"] or "",
                      r["spender"] or "", r["kind"] or "", f"[yellow]{last}[/yellow]" if stale else (last or "-"),
                      (r["last_fetched_at"] or "-")[:16], f"[red]{r['last_error']}[/red]" if r["last_error"] else "")
    console.print(table)


@fetch.command("set-account")
@click.argument("account_id")
@click.option("--card", default=None, help='Card name used on expenses, e.g. "Alex Amex".')
@click.option("--spender", default=None, help="Whose card it is (a HOUSEHOLD_MEMBERS name).")
@click.option("--kind", type=click.Choice(["card", "bank"]), default=None)
@click.option("--disable/--enable", "disabled", default=None, help="Stop or resume fetching this account.")
def set_account_cmd(account_id: str, card: Optional[str], spender: Optional[str], kind: Optional[str],
                    disabled: Optional[bool]) -> None:
    """Name a fetched account's card and person (its existing expenses follow)."""
    from ..db.session import get_engine
    from .importer import set_account

    try:
        changed = set_account(get_engine(), account_id, card=card, spender=spender, kind=kind,
                              enabled=None if disabled is None else not disabled)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"Updated; {changed} existing expenses relabelled")
