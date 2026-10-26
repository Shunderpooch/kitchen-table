"""CLI interface for the expenses processing pipeline."""

from datetime import datetime
from pathlib import Path
from typing import Optional
import sys

import click
from rich.console import Console
from rich.table import Table

from .household import household_sheet, statement_sources
from .db import (
    init_db,
    session_scope,
    Expense,
    SourceFile,
    Category,
    Subtype,
    StatementSource,
    ValidationQueueItem,
)
from .ingest import read_expenses_csv, read_expenses_excel, read_expenses_pdf, summarize_input_file
from .categorize import (
    categorize_expenses_batch,
    get_pending_validations,
    find_existing_category_assignment,
)
from .validate import find_all_duplicates_in_batch
from .scans.cli import scans as scans_cli

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

console = Console(force_terminal=True, legacy_windows=False)


@click.group()
@click.option('--db', 'db_path', type=click.Path(), default=None, help='Path to database file')
@click.pass_context
def cli(ctx: click.Context, db_path: Optional[str]) -> None:
    """Expenses Processing Pipeline - CLI Interface."""
    ctx.ensure_object(dict)
    ctx.obj['db_path'] = db_path


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize the database (create tables)."""
    db_path = ctx.obj.get('db_path')
    init_db(db_path)
    console.print("[green]Database initialized successfully![/green]")


@cli.command()
@click.argument('file_path', type=click.Path(exists=True))
@click.option('--skip-duplicates', is_flag=True, help='Skip duplicate detection')
@click.option('--skip-categorization', is_flag=True, help='Skip auto-categorization')
@click.option('--card', default=None, help='Card/statement source name (e.g., "Discover", "Amex")')
@click.option('--spender', default=None, help='Override spender for the imported statement (a HOUSEHOLD_MEMBERS name)')
@click.pass_context
def process(
    ctx: click.Context,
    file_path: str,
    skip_duplicates: bool,
    skip_categorization: bool,
    card: Optional[str],
    spender: Optional[str],
) -> None:
    """Process a CSV or Excel file of expenses."""
    db_path = ctx.obj.get('db_path')
    file_path = Path(file_path)
    init_db(db_path)

    # Determine file type
    suffix = file_path.suffix.lower()
    if suffix not in ['.csv', '.xlsx', '.xls', '.pdf']:
        console.print(f"[red]Unsupported file type: {suffix}[/red]")
        return

    with session_scope(db_path) as session:
        # Create source file record
        source_file = SourceFile(
            filename=file_path.name,
            file_type=suffix[1:],  # Remove the dot
            status='processing',
        )
        session.add(source_file)
        session.flush()  # Get ID

        print(f"Processing: {file_path.name}")

        # Read expenses
        print("Reading file...")
        expenses = []
        if suffix == '.csv':
            reader = read_expenses_csv
        elif suffix in ['.xlsx', '.xls']:
            reader = read_expenses_excel
        else:
            reader = read_expenses_pdf
        ingestion_summary = summarize_input_file(file_path, card_name=card, spender_name=spender)

        for expense in reader(file_path, source_file, card_name=card, spender_name=spender):
            expenses.append(expense)

        print(f"Read {len(expenses)} expenses")

        # Check for duplicates
        if not skip_duplicates:
            print("Checking for duplicates...")
            dup_results = find_all_duplicates_in_batch(expenses, session)
            print(
                f"  Found {dup_results['exact_duplicates']} exact duplicates, "
                f"{dup_results['fuzzy_duplicates']} fuzzy duplicates"
            )
            # Filter out duplicates
            expenses = [e for e in expenses if not e.is_duplicate]

        # Add expenses to session
        for expense in expenses:
            session.add(expense)
        session.flush()

        # Categorize expenses
        categorized_count = 0
        needs_validation_count = 0
        if not skip_categorization:
            print("Categorizing expenses...")
            cat_results = categorize_expenses_batch(expenses, session)
            categorized_count = cat_results['categorized']
            needs_validation_count = cat_results['needs_validation']
            print(
                f"  Categorized: {categorized_count}, "
                f"Needs validation: {needs_validation_count}"
            )

        # Update source file status
        source_file.row_count = len(expenses)
        source_file.processed_at = datetime.now()
        source_file.status = 'completed'

        # Calculate duplicates for summary
        dup_count = dup_results['exact_duplicates'] + dup_results['fuzzy_duplicates'] if not skip_duplicates else 0

        # Output machine-parseable summary
        print(f"Processed {len(expenses)} expenses")
        print(f"Auto-categorized: {categorized_count}")
        print(f"Needs validation: {needs_validation_count}")
        print(f"Duplicates found: {dup_count}")
        print(f"Payments/credits skipped: {ingestion_summary['payments_or_credits_skipped']}")
        print(f"Successfully processed {len(expenses)} expenses!")


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show pipeline status and statistics."""
    db_path = ctx.obj.get('db_path')

    with session_scope(db_path) as session:
        # Count expenses
        total_expenses = session.query(Expense).count()
        validated = session.query(Expense).filter_by(needs_validation=False).count()
        pending = session.query(Expense).filter_by(needs_validation=True).count()
        duplicates = session.query(Expense).filter_by(is_duplicate=True).count()

        # Validation queue
        queue_pending = session.query(ValidationQueueItem).filter_by(status='pending').count()

        # Categories
        category_count = session.query(Category).count()
        subtype_count = session.query(Subtype).count()

        # Statement sources
        sources = session.query(StatementSource).all()

        # Display stats
        table = Table(title="Pipeline Status")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Total Expenses", str(total_expenses))
        table.add_row("Validated", str(validated))
        table.add_row("Pending Validation", str(pending))
        table.add_row("Duplicates", str(duplicates))
        table.add_row("Validation Queue", str(queue_pending))
        table.add_row("Categories", str(category_count))
        table.add_row("Subtypes", str(subtype_count))

        console.print(table)

        # Statement sources status
        if sources:
            console.print("\n[bold]Statement Sources:[/bold]")
            source_table = Table()
            source_table.add_column("Source", style="cyan")
            source_table.add_column("Last Import", style="green")
            source_table.add_column("Status", style="yellow")

            for source in sources:
                status_str = "[red]STALE[/red]" if source.is_stale else "[green]OK[/green]"
                last_import = str(source.last_import_date) if source.last_import_date else "Never"
                source_table.add_row(source.name, last_import, status_str)

            console.print(source_table)


@cli.command()
@click.option('--limit', default=20, help='Number of items to show')
@click.pass_context
def validation_queue(ctx: click.Context, limit: int) -> None:
    """Show pending validation queue items."""
    db_path = ctx.obj.get('db_path')

    with session_scope(db_path) as session:
        pending = get_pending_validations(session, limit)

        if not pending:
            console.print("[green]No pending validations![/green]")
            return

        table = Table(title=f"Pending Validations ({len(pending)} shown)")
        table.add_column("ID", style="dim")
        table.add_column("Date", style="cyan")
        table.add_column("Merchant", style="green")
        table.add_column("Amount", style="yellow")
        table.add_column("Current Type", style="blue")
        table.add_column("Reason", style="red")

        for item in pending:
            expense = item.expense
            table.add_row(
                str(item.id),
                str(expense.date),
                expense.merchant or '-',
                f"${expense.amount:.2f}",
                expense.type,
                item.reason or '-',
            )

        console.print(table)


@cli.command("restore-household-categories")
@click.option(
    "--filename-match",
    default=None,
    help="Source filename to treat as canonical household import (default: HOUSEHOLD_SHEET)",
)
@click.pass_context
def restore_household_categories(ctx: click.Context, filename_match: str) -> None:
    """Relink canonical household imports and remove misleading validation warnings."""
    db_path = ctx.obj.get('db_path')
    filename_match = filename_match or household_sheet()

    with session_scope(db_path) as session:
        source_file = session.query(SourceFile).filter_by(filename=filename_match).first()
        if not source_file:
            console.print(f"[red]Source file not found: {filename_match}[/red]")
            return

        expenses = session.query(Expense).filter_by(source_file_id=source_file.id).all()

        linked_count = 0
        cleared_validation_count = 0
        cleared_queue_count = 0

        for expense in expenses:
            category, subtype = find_existing_category_assignment(expense, session)
            if category:
                if expense.category_id != category.id:
                    linked_count += 1
                expense.category_id = category.id
                expense.category_rel = category

                if subtype:
                    expense.subtype_id = subtype.id
                    expense.subtype_rel = subtype
                else:
                    expense.subtype_id = None
                    expense.subtype_rel = None

            if expense.needs_validation:
                cleared_validation_count += 1
            expense.needs_validation = False

            queue_item = session.query(ValidationQueueItem).filter_by(expense_id=expense.id).first()
            if queue_item:
                session.delete(queue_item)
                cleared_queue_count += 1

        console.print(
            "[green]Household categories restored.[/green]\n"
            f"Linked categories: {linked_count}\n"
            f"Cleared validation flags: {cleared_validation_count}\n"
            f"Removed queue items: {cleared_queue_count}"
        )


@cli.command()
@click.pass_context
def stale_sources(ctx: click.Context) -> None:
    """Show statement sources that need updating."""
    db_path = ctx.obj.get('db_path')

    with session_scope(db_path) as session:
        sources = session.query(StatementSource).all()
        stale = [s for s in sources if s.is_stale]

        if not stale:
            console.print("[green]All statement sources are up to date![/green]")
            return

        console.print("[yellow]The following statement sources need updating:[/yellow]\n")

        for source in stale:
            days_old = "Never imported" if not source.last_import_date else \
                f"{(datetime.now().date() - source.last_import_date).days} days old"
            console.print(f"  [red]•[/red] {source.name}: {days_old}")

        console.print("\n[blue]Please download and process the latest statements.[/blue]")


@cli.command()
@click.pass_context
def list_categories(ctx: click.Context) -> None:
    """List all categories and subtypes."""
    db_path = ctx.obj.get('db_path')

    with session_scope(db_path) as session:
        categories = session.query(Category).order_by(Category.name).all()

        if not categories:
            console.print("[yellow]No categories found. Run 'seed' to populate.[/yellow]")
            return

        for category in categories:
            console.print(f"[bold cyan]{category.name}[/bold cyan]")
            for subtype in category.subtypes:
                console.print(f"  └─ {subtype.name}")


@cli.command()
@click.option('--csv-file', type=click.Path(exists=True), help='CSV file to extract categories from')
@click.pass_context
def seed(ctx: click.Context, csv_file: Optional[str]) -> None:
    """Seed the database with categories from existing data or defaults."""
    db_path = ctx.obj.get('db_path')

    # Initialize database first
    init_db(db_path)

    with session_scope(db_path) as session:
        if csv_file:
            # Extract categories from CSV
            console.print(f"[blue]Extracting categories from: {csv_file}[/blue]")
            _seed_from_csv(session, Path(csv_file))
        else:
            # Use default categories
            console.print("[blue]Seeding with default categories...[/blue]")
            _seed_default_categories(session)

        # Seed statement sources
        _seed_statement_sources(session)

        console.print("[green]Seeding complete![/green]")


def _seed_from_csv(session, csv_path: Path) -> None:
    """Extract and seed categories from a CSV file."""
    import csv
    from collections import defaultdict

    categories_map = defaultdict(set)

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            type_val = row.get('Type', '').strip()
            subtype_val = row.get('Subtype', '').strip()

            # Skip empty or raw bank transaction types
            if not type_val:
                continue

            # Skip raw bank transaction strings (contain state codes, etc.)
            from .ingest import is_uncategorized_bank_transaction
            if is_uncategorized_bank_transaction(type_val):
                continue

            categories_map[type_val].add(subtype_val)

    # Create categories and subtypes
    for cat_name, subtypes in sorted(categories_map.items()):
        # Check if category exists
        existing = session.query(Category).filter_by(name=cat_name).first()
        if not existing:
            category = Category(name=cat_name)
            session.add(category)
            session.flush()
        else:
            category = existing

        # Add subtypes
        for subtype_name in sorted(subtypes):
            if not subtype_name:
                continue
            existing_subtype = session.query(Subtype).filter_by(
                category_id=category.id, name=subtype_name
            ).first()
            if not existing_subtype:
                subtype = Subtype(category_id=category.id, name=subtype_name)
                session.add(subtype)

    console.print(f"  Created {len(categories_map)} categories")


def _seed_default_categories(session) -> None:
    """Seed with default categories."""
    default_categories = {
        'Food and Drink - Dining Out': ['Breakfast', 'Lunch', 'Dinner', 'Cafe', 'Fast Food', 'Dessert'],
        'Food and Drink - Grocery': ['Grocery Store', 'Convenience', 'Coffee', 'Tea', 'Alcohol'],
        'Travel': ['Airfare', 'Hotel', 'Rental Car', 'Gasoline', 'Parking', 'Tolls', 'Ride Sharing'],
        'Subscriptions': ['Streaming', 'Music', 'Gaming', 'Cloud Storage', 'Generative AI'],
        'Electronics': ['Computers', 'Cellular Phone', 'Accessories', 'Audio Equipment'],
        'Home Improvement': ['Tools', 'Electrical', 'Plumbing', 'Garden', 'Lighting'],
        'Household Supplies': ['Cleaning', 'Kitchen', 'Batteries', 'Linens'],
        'Clothing': ['Everyday Clothes', 'Shoes', 'Accessories', 'Jewelry'],
        'Health Care': ['Prescriptions', 'Copay', 'Dental', 'Optometrist', 'Urgent Care'],
        'Automotive': ['Gasoline', 'Parts', 'Insurance', 'Repairs', 'Washes'],
        'Utilities': ['Electricity', 'Heat', 'Water', 'Internet'],
    }

    for cat_name, subtypes in default_categories.items():
        category = Category(name=cat_name)
        session.add(category)
        session.flush()

        for subtype_name in subtypes:
            subtype = Subtype(category_id=category.id, name=subtype_name)
            session.add(subtype)


def _seed_statement_sources(session) -> None:
    """Seed statement sources: each household member's cards and accounts (see household.py)."""
    sources = statement_sources()

    for name, source_type in sources:
        existing = session.query(StatementSource).filter_by(name=name).first()
        if not existing:
            source = StatementSource(name=name, source_type=source_type)
            session.add(source)

    console.print(f"  Created {len(sources)} statement sources")


cli.add_command(scans_cli)


if __name__ == '__main__':
    cli()
