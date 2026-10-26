"""CSV and Excel file ingestion for expenses."""

import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Generator

import pandas as pd
from pypdf import PdfReader

from ..db import Expense, SourceFile
from ..household import member_a, member_b

# "Alex Smith (Card Number Ending In 1234)" printed inside PayPal Credit rows.
CARDHOLDER_LINE = r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*)+ \(Card Number Ending In \d+\)"


def spend_column(row: dict, which: str):
    """A member's share from a household-sheet row: "<Name> Spend" or "Member A/B Spend"."""
    name = member_a() if which == "a" else member_b()
    for column in ([f"{name} Spend"] if name else []) + [f"Member {which.upper()} Spend"]:
        if row.get(column) not in (None, ""):
            return parse_amount(row.get(column))
    return None


def parse_amount(amount_str: str | float | int | None) -> Decimal | None:
    """Parse an amount string to Decimal, handling currency symbols and commas.

    Examples:
        "$1,234.56" -> Decimal("1234.56")
        "-$50.00" -> Decimal("-50.00")
        "1234.56" -> Decimal("1234.56")
    """
    if amount_str is None or (isinstance(amount_str, float) and pd.isna(amount_str)):
        return None

    if isinstance(amount_str, (int, float)):
        return Decimal(str(amount_str))

    # Remove currency symbols and commas
    cleaned = re.sub(r'[$,]', '', str(amount_str).strip())

    # Handle negative amounts (could be "-$50" or "($50)")
    if cleaned.startswith('(') and cleaned.endswith(')'):
        cleaned = '-' + cleaned[1:-1]

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(date_str: str | datetime | None) -> datetime | None:
    """Parse a date string to datetime.

    Handles formats like:
        "10/3/2023" (M/D/YYYY)
        "10/3/23" (M/D/YY)
        "2023-10-03" (YYYY-MM-DD)
    """
    if date_str is None or (isinstance(date_str, float) and pd.isna(date_str)):
        return None

    if isinstance(date_str, datetime):
        return date_str

    if isinstance(date_str, pd.Timestamp):
        return date_str.to_pydatetime()

    # Try common date formats
    formats = [
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(str(date_str).strip(), fmt)
        except ValueError:
            continue

    return None


def parse_split(split_str: str | float | int | None) -> Decimal:
    """Parse a split ratio (0.0 to 1.0)."""
    if split_str is None or (isinstance(split_str, float) and pd.isna(split_str)):
        return Decimal("1.0")

    try:
        value = Decimal(str(split_str))
        # Ensure it's between 0 and 1
        if value < 0:
            return Decimal("0")
        if value > 1:
            return Decimal("1.0")
        return value
    except InvalidOperation:
        return Decimal("1.0")


def parse_bool(value: str | bool | float | None) -> bool | None:
    """Parse a boolean value."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    if isinstance(value, bool):
        return value

    str_value = str(value).strip().lower()
    if str_value in ('true', '1', 'yes', 'y'):
        return True
    if str_value in ('false', '0', 'no', 'n', ''):
        return False

    return None


def compute_content_hash(row: dict) -> str:
    """Compute a hash of the row content for duplicate detection.

    Uses date, merchant, amount, and spender to create a unique identifier.
    """
    parts = [
        str(row.get('Date', '')),
        str(row.get('Merchant', '')).lower(),
        str(row.get('Amount', '')),
        str(row.get('Spender', '')),
    ]
    content = '|'.join(parts)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def is_uncategorized_bank_transaction(type_value: str) -> bool:
    """Check if a type value is a raw bank transaction string (not a canonical category).

    Raw bank transactions typically contain location info like state codes,
    or start with payment prefixes like "GglPay", "PAYPAL *", etc.
    """
    if not type_value:
        return False

    type_value = type_value.strip()

    # Pattern indicators of raw bank transactions
    raw_patterns = [
        r'\s{2,}[A-Z]{2}$',  # Ends with state code like "MA", "CA"
        r'^GglPay\s',  # Google Pay transactions
        r'^PAYPAL \*',  # PayPal transactions
        r'^\d{5,}',  # Starts with numbers (transaction IDs)
        r'\d{10}',  # Contains long number sequences
        r'AMZN\.COM/BILL',  # Amazon billing
        r'800-\d{3}-\d{4}',  # Phone numbers
    ]

    for pattern in raw_patterns:
        if re.search(pattern, type_value, re.IGNORECASE):
            return True

    return False


def detect_csv_format(df: pd.DataFrame) -> str:
    """Detect the format of a CSV file based on its columns.

    Returns:
        'household' - The household expenses spreadsheet format
        'discover' - Discover credit card statement format
        'amex' - American Express statement format
        'generic_bank' - Generic bank/credit card statement
        'unknown' - Unknown format
    """
    columns = set(col.lower().strip() for col in df.columns)

    # Household expense spreadsheet format
    if 'type' in columns and 'subtype' in columns and 'merchant' in columns:
        return 'household'

    # Discover format: Trans. Date, Post Date, Description, Amount, Category
    if 'trans. date' in columns and 'description' in columns and 'category' in columns:
        return 'discover'

    # Amex format: Date, Description, Amount (or similar)
    if 'date' in columns and 'description' in columns and 'amount' in columns:
        return 'amex'

    # Generic bank with transaction date
    if ('transaction date' in columns or 'posting date' in columns) and 'amount' in columns:
        return 'generic_bank'

    return 'unknown'


def is_malformed_household_tail_row(row: dict) -> bool:
    """Detect broken statement-like rows appended to the household spreadsheet.

    These rows are shifted out of the canonical household column layout. They
    currently show up with a raw transaction string in `Type`, a person name in
    `Subtype`, and a negative numeric marker in `Merchant`.
    """
    subtype = str(row.get('Subtype', '')).strip().upper()
    merchant = str(row.get('Merchant', '')).strip()
    spender = str(row.get('Spender', '')).strip()
    card = str(row.get('Card', '')).strip()

    return (
        bool(re.fullmatch(r"[A-Z][A-Z'.-]+(?: [A-Z][A-Z'.-]+)+", subtype))  # a cardholder's name
        and bool(re.fullmatch(r'-\d+', merchant))
        and not spender
        and not card
    )


def normalize_discover_row(row: dict, card_name: str = "Discover") -> dict:
    """Convert a Discover statement row to normalized expense format."""
    description = str(row.get('Description', '')).strip()
    category = str(row.get('Category', '')).strip()

    # Parse the description to extract merchant name
    # Discover descriptions often have format like "PAYPAL *UBER 888-221-1161 CA"
    merchant = description.split(' ')[0] if description else None

    # For more common patterns, extract better merchant names
    if description.startswith('PAYPAL *'):
        parts = description.replace('PAYPAL *', '').split(' ')
        merchant = 'PayPal - ' + parts[0] if parts else 'PayPal'
    elif 'UBER EATS' in description.upper():
        merchant = 'Uber Eats'
    elif 'UBER' in description.upper():
        merchant = 'Uber'
    elif 'BURGER KING' in description.upper():
        merchant = 'Burger King'
    elif 'E-ZPASS' in description.upper():
        merchant = 'E-ZPass'
    elif 'PROGRESSIVE' in description.upper():
        merchant = 'Progressive Insurance'
    else:
        # Clean up merchant name - take first part before numbers/phone
        merchant = re.sub(r'\s+\d{3}-\d{3}-\d{4}.*', '', description)
        merchant = re.sub(r'\s+[A-Z]{2}$', '', merchant)  # Remove state codes
        merchant = merchant.strip()

    return {
        'Date': row.get('Trans. Date', ''),
        'Type': description,  # Use description as type for validation
        'Subtype': '',
        'Merchant': merchant,
        'Original Input Name': merchant,
        'Amount': row.get('Amount', ''),
        'Spender': '',  # Will be set based on card
        'Card': card_name,
        'Split': '1.0',
        'Member B Spend': '',
        'Member A Spend': '',
    }


def normalize_amex_row(row: dict, card_name: str = "Amex") -> dict:
    """Convert an Amex statement row to normalized expense format."""
    description = str(row.get('Description', '')).strip()

    return {
        'Date': row.get('Date', ''),
        'Type': description,
        'Subtype': '',
        'Merchant': description,
        'Original Input Name': description,
        'Amount': row.get('Amount', ''),
        'Spender': '',
        'Card': card_name,
        'Split': '1.0',
        'Member B Spend': '',
        'Member A Spend': '',
    }


def normalize_generic_bank_row(row: dict, card_name: str = "Bank") -> dict:
    """Convert a generic bank statement row to normalized expense format."""
    # Try various column names for description
    description = ''
    for col in ['Description', 'Memo', 'Transaction', 'Details', 'Payee']:
        if col in row and str(row.get(col, '')).strip():
            description = str(row.get(col, '')).strip()
            break

    # Try various column names for date
    date_val = ''
    for col in ['Transaction Date', 'Posting Date', 'Date', 'Trans Date']:
        if col in row and str(row.get(col, '')).strip():
            date_val = str(row.get(col, '')).strip()
            break

    return {
        'Date': date_val,
        'Type': description,
        'Subtype': '',
        'Merchant': description,
        'Original Input Name': description,
        'Amount': row.get('Amount', ''),
        'Spender': '',
        'Card': card_name,
        'Split': '1.0',
        'Member B Spend': '',
        'Member A Spend': '',
    }


BOFA_PDF_SECTION_HEADERS = {
    "payments and other credits": "Payments and Other Credits",
    "purchases and adjustments": "Purchases and Adjustments",
    "balance transfers": "Balance Transfers",
    "fees": "Fees",
    "interest charged": "Interest Charged",
}


def infer_statement_card_name(
    file_path: Path,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> str:
    """Infer a card/source name from either the explicit selection or filename."""
    normalized_card = card_name.strip() if card_name else None
    normalized_spender = spender_name.strip() if spender_name else None

    if normalized_card:
        if normalized_card == "BofA" and normalized_spender:
            return f"{normalized_spender} Bofa Visa"
        if normalized_card == "PenFed" and normalized_spender:
            return f"{normalized_spender} Penfed Visa"
        return normalized_card

    filename = file_path.name.lower()
    if 'discover' in filename:
        return "Discover"
    if 'amex' in filename or 'american' in filename:
        return "Amex"
    if 'citi' in filename:
        return "Citi"
    if 'bofa' in filename or 'bank of america' in filename or 'estmt' in filename:
        if normalized_spender:
            return f"{normalized_spender} Bofa Visa"
        return "BofA"
    if 'penfed' in filename:
        if normalized_spender:
            return f"{normalized_spender} Penfed Visa"
        return "PenFed"
    if 'paypal' in filename:
        return "PayPal Credit"
    if 'chase' in filename:
        return "Chase"
    return "Unknown"


def parse_bofa_pdf_rows(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> list[dict]:
    """Extract normalized transaction rows from a Bank of America PDF statement."""
    file_path = Path(file_path)
    resolved_card_name = infer_statement_card_name(file_path, card_name, spender_name)
    reader = PdfReader(str(file_path))
    all_text = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)

    if "bank of america" not in all_text.lower():
        raise ValueError(f"Unsupported PDF statement format: {file_path.name}")

    closing_match = re.search(r"Statement Closing Date\s+(\d{2}/\d{2}/\d{4})", all_text)
    closing_date = (
        datetime.strptime(closing_match.group(1), "%m/%d/%Y") if closing_match else None
    )

    rows: list[dict] = []
    current_section: str | None = None

    for page in reader.pages:
        page_text = page.extract_text(extraction_mode="layout") or ""
        for raw_line in page_text.splitlines():
            clean_line = raw_line.strip()
            if not clean_line:
                continue

            lower_line = clean_line.lower()
            if lower_line in BOFA_PDF_SECTION_HEADERS:
                current_section = BOFA_PDF_SECTION_HEADERS[lower_line]
                continue

            if "total " in lower_line and " for this period" in lower_line:
                continue

            normalized_line = re.sub(r"\s{2,}", " | ", clean_line)
            parts = [part.strip() for part in normalized_line.split("|") if part.strip()]
            if len(parts) < 4:
                continue

            if not re.fullmatch(r"\d{2}/\d{2}", parts[0]) or not re.fullmatch(r"\d{2}/\d{2}", parts[1]):
                continue

            amount_token = parts[-1].replace("$", "")
            if not re.fullmatch(r"-?\d[\d,]*\.\d{2}", amount_token):
                continue

            description = parts[2]
            transaction_month = int(parts[0].split("/")[0])
            transaction_year = closing_date.year if closing_date else datetime.now().year
            if closing_date and transaction_month > closing_date.month:
                transaction_year -= 1

            transaction_date = f"{parts[0]}/{transaction_year}"
            amount_value = amount_token
            if current_section == "Payments and Other Credits" and not amount_value.startswith("-"):
                amount_value = f"-{amount_value}"

            rows.append(
                {
                    "Date": transaction_date,
                    "Type": description,
                    "Subtype": "",
                    "Merchant": description,
                    "Original Input Name": description,
                    "Amount": amount_value,
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": current_section or "Transactions",
                }
            )

    return rows


def parse_penfed_pdf_rows(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> list[dict]:
    """Extract normalized transaction rows from a PenFed PDF statement."""
    file_path = Path(file_path)
    resolved_card_name = infer_statement_card_name(file_path, card_name, spender_name)
    reader = PdfReader(str(file_path))
    all_text = "\n".join((page.extract_text() or "") for page in reader.pages)

    if "penfed" not in all_text.lower():
        raise ValueError(f"Unsupported PDF statement format: {file_path.name}")

    closing_match = re.search(r"Statement Closing Date\s+(\d{2}/\d{2}/\d{4})", all_text)
    closing_date = (
        datetime.strptime(closing_match.group(1), "%m/%d/%Y") if closing_match else None
    )

    rows: list[dict] = []
    pattern = re.compile(
        r"(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+(-?\$?\d[\d,]*\.\d{2})(?=\s+\d{2}/\d{2}\s+\d{2}/\d{2}\s+|$)"
    )

    for page in reader.pages:
        page_text = page.extract_text() or ""
        if "Transactions" not in page_text:
            continue

        transaction_section = page_text.split("Transactions", 1)[1]
        for stop_marker in [
            "Total fees charged in",
            "TOTAL FEES FOR THIS PERIOD",
            "Your  is the annual interest rate on your account.",
        ]:
            if stop_marker in transaction_section:
                transaction_section = transaction_section.split(stop_marker, 1)[0]
                break

        normalized_section = re.sub(r"\s+", " ", transaction_section).strip()

        for match in pattern.finditer(normalized_section):
            transaction_date_mmdd = match.group(1)
            description = re.sub(r"\s+", " ", match.group(3)).strip()
            amount_value = match.group(4).replace("$", "")

            transaction_month = int(transaction_date_mmdd.split("/")[0])
            transaction_year = closing_date.year if closing_date else datetime.now().year
            if closing_date and transaction_month > closing_date.month:
                transaction_year -= 1

            rows.append(
                {
                    "Date": f"{transaction_date_mmdd}/{transaction_year}",
                    "Type": description,
                    "Subtype": "",
                    "Merchant": description,
                    "Original Input Name": description,
                    "Amount": amount_value,
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Transactions",
                }
            )

    return rows


DISCOVER_PDF_CATEGORIES = [
    "Medical Services",
    "Travel/Entertainment",
    "Home Improvement",
    "Supermarkets",
    "Restaurants",
    "Merchandise",
    "Gasoline",
    "Services",
]


def parse_discover_pdf_rows(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> list[dict]:
    """Extract normalized transaction rows from a Discover PDF statement."""
    file_path = Path(file_path)
    resolved_card_name = infer_statement_card_name(file_path, card_name, spender_name)
    reader = PdfReader(str(file_path))
    lines: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        lines.extend(line.strip() for line in text.splitlines())

    if not any("discover it card ending" in line.lower() for line in lines):
        raise ValueError(f"Unsupported PDF statement format: {file_path.name}")

    closing_date = None
    for line in lines:
        match = re.search(r"OPEN TO CLOSE DATE:\s+\d{2}/\d{2}/\d{4}\s*-\s*(\d{2}/\d{2}/\d{4})", line, re.IGNORECASE)
        if match:
            closing_date = parse_date(match.group(1))
            break

    rows: list[dict] = []
    pending_lines: list[str] | None = None

    def flush_pending():
        nonlocal pending_lines
        if not pending_lines:
            return

        filtered_parts: list[str] = []
        for index, part in enumerate(pending_lines):
            if not part:
                continue
            if index > 0 and (
                re.fullmatch(r"[A-Z0-9]{8,}", part)
                or re.fullmatch(r"\d{6,}", part)
            ):
                continue
            filtered_parts.append(part)

        if not filtered_parts:
            pending_lines = None
            return

        first_line = filtered_parts[0]
        combined = " ".join(filtered_parts)
        combined = re.sub(r"\s+", " ", combined).strip()

        payment_match = re.match(r"^(\d{2}/\d{2})\s+(.+?)\s+-\$(\d[\d,]*\.\d{2})$", combined)
        if payment_match:
            normalized = normalize_discover_row(
                {
                    "Trans. Date": expand_statement_mmdd(payment_match.group(1), closing_date),
                    "Description": payment_match.group(2).strip(),
                    "Category": "",
                    "Amount": f"-{payment_match.group(3).replace(',', '')}",
                },
                resolved_card_name,
            )
            rows.append(normalized)
            pending_lines = None
            return

        amount_match = re.search(r"\$([\d,]+\.\d{2})$", combined)
        if not amount_match:
            pending_lines = None
            return

        transaction_date = first_line[:5]
        description_block = combined[5:amount_match.start()].strip()
        description_block = re.sub(r"\bGOOGLE PAY ENDING IN \d+\b", "", description_block, flags=re.IGNORECASE)
        description_block = re.sub(r"\b[A-Z0-9]{8,}\b$", "", description_block).strip()

        category_value = ""
        for candidate in DISCOVER_PDF_CATEGORIES:
            suffix = f" {candidate}"
            if description_block.endswith(suffix):
                description_block = description_block[: -len(suffix)].strip()
                category_value = candidate
                break

        normalized = normalize_discover_row(
            {
                "Trans. Date": expand_statement_mmdd(transaction_date, closing_date),
                "Description": description_block,
                "Category": category_value,
                "Amount": amount_match.group(1).replace(",", ""),
            },
            resolved_card_name,
        )
        rows.append(normalized)
        pending_lines = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        if re.match(r"^\d{2}/\d{2}\s+", line):
            flush_pending()
            pending_lines = [line]
            continue

        if pending_lines is None:
            continue

        lower_line = line.lower()
        if (
            lower_line.startswith("page ")
            or lower_line.startswith("open to close date:")
            or lower_line.startswith("continued on next page")
            or lower_line.startswith("transactions continued")
            or lower_line.startswith("total fees")
            or lower_line.startswith("total interest")
            or lower_line.startswith("cashback bonus")
            or lower_line.startswith("previous balance")
            or lower_line.startswith("earned this period")
            or lower_line.startswith("redeemed this period")
            or lower_line.startswith("rewards")
            or lower_line.startswith("google pay ending in")
            or line == "Ž"
            or line == "-"
        ):
            continue

        pending_lines.append(line)

    flush_pending()
    return rows


def clean_paypal_credit_description(description: str) -> str:
    """Normalize a PayPal Credit statement description into a merchant-like label."""
    cleaned = re.sub(r"\bNo Interest If Paid In Full\b", "", description, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bDeferred\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{3}-\d{3}-\d{4}\b", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def infer_statement_closing_date(file_path: Path) -> datetime | None:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", file_path.stem)
    if not match:
        return None

    try:
        return datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except ValueError:
        return None


def expand_statement_mmdd(transaction_date: str, closing_date: datetime | None) -> str:
    month, day = [int(part) for part in transaction_date.split("/")]
    transaction_year = closing_date.year if closing_date else datetime.now().year
    if closing_date and month > closing_date.month:
        transaction_year -= 1
    return f"{month:02d}/{day:02d}/{transaction_year}"


def parse_paypal_credit_pdf_rows(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> list[dict]:
    """Extract normalized transaction rows from a PayPal Credit PDF statement."""
    file_path = Path(file_path)
    resolved_card_name = infer_statement_card_name(file_path, card_name, spender_name)
    reader = PdfReader(str(file_path))
    all_text = "\n".join((page.extract_text() or "") for page in reader.pages)
    lowered = all_text.lower()

    if "paypal credit" not in lowered and "paypal credit/syncb" not in lowered:
        raise ValueError(f"Unsupported PDF statement format: {file_path.name}")

    closing_date = infer_statement_closing_date(file_path)
    rows: list[dict] = []

    current_activity_match = re.search(
        r"CURRENT ACTIVITY\s+PAYMENTS\s*&\s*CREDITS(?P<payments>.+?)Total Payments\s*&\s*Credits.+?PURCHASES\s*&\s*ADJUSTMENTS(?P<purchases>.+?)Total Purchases\s*&\s*Adjustments",
        all_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if current_activity_match:
        payments_section = re.sub(r"\s+", " ", current_activity_match.group("payments")).strip()
        purchases_section = re.sub(r"\s+", " ", current_activity_match.group("purchases")).strip()

        payment_pattern = re.compile(
            r"(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+([A-Z0-9]+)\s+(.+?)\s+-\$(\d[\d,]*\.\d{2})"
        )
        for match in payment_pattern.finditer(payments_section):
            transaction_date = match.group(1)
            description = re.sub(r"\s{2,}", " ", match.group(4)).strip()
            amount_value = f"-{match.group(5).replace(',', '')}"
            rows.append(
                {
                    "Date": transaction_date,
                    "Type": description,
                    "Subtype": "",
                    "Merchant": clean_paypal_credit_description(description),
                    "Original Input Name": description,
                    "Amount": amount_value,
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Payments and Credits",
                }
            )

        purchase_pattern = re.compile(
            r"(\d{2}/\d{2}/\d{2})\s+(\d{2}/\d{2}/\d{2})\s+([A-Z0-9]+)\s+([A-Za-z]+)\s+(.+?)\s+\$(\d[\d,]*\.\d{2})"
        )
        for match in purchase_pattern.finditer(purchases_section):
            transaction_date = match.group(1)
            type_value = match.group(4).strip()
            description = re.sub(r"\s{2,}", " ", match.group(5)).strip()
            amount_value = match.group(6).replace(",", "")
            merchant = clean_paypal_credit_description(description)
            rows.append(
                {
                    "Date": transaction_date,
                    "Type": f"{type_value} {description}".strip(),
                    "Subtype": "",
                    "Merchant": merchant,
                    "Original Input Name": description,
                    "Amount": amount_value,
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Purchases and Adjustments",
                }
            )

        return rows

    transaction_details_match = re.search(
        r"Transaction details(?P<details>.+?)Total fees charged this period",
        all_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not transaction_details_match:
        raise ValueError(f"Could not find current activity section in: {file_path.name}")

    normalized_details = re.sub(r"\s+", " ", transaction_details_match.group("details")).strip()

    payments_match = re.search(
        r"Payments\s+\$[\d,]+\.\d{2}-(?P<section>.+?)(?:Other credits\s+\$[\d,]+\.\d{2}-|Purchases and other debits\s+\$[\d,]+\.\d{2})",
        normalized_details,
        flags=re.IGNORECASE,
    )
    if payments_match:
        payment_pattern = re.compile(
            r"(\d{2}/\d{2})\s+[A-Z0-9]+\s+(.+?)\s+\$([\d,]+\.\d{2})-(?=\d{2}/\d{2}\s+[A-Z0-9]+|$)"
        )
        for match in payment_pattern.finditer(payments_match.group("section")):
            description = re.sub(r"\s+", " ", match.group(2)).strip()
            rows.append(
                {
                    "Date": expand_statement_mmdd(match.group(1), closing_date),
                    "Type": description,
                    "Subtype": "",
                    "Merchant": clean_paypal_credit_description(description),
                    "Original Input Name": description,
                    "Amount": f"-{match.group(3).replace(',', '')}",
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Payments and Credits",
                }
            )

    other_credits_match = re.search(
        r"Other credits\s+\$[\d,]+\.\d{2}-(?P<section>.+?)Purchases and other debits\s+\$[\d,]+\.\d{2}",
        normalized_details,
        flags=re.IGNORECASE,
    )
    if other_credits_match:
        other_credit_pattern = re.compile(
            r"(\d{2}/\d{2})\s+[A-Z0-9]+\s+(.+?)\s+\$([\d,]+\.\d{2})-(?=\d{2}/\d{2}\s+[A-Z0-9]+|TOTAL\s+\$[\d,]+\.\d{2}|$)"
        )
        for match in other_credit_pattern.finditer(other_credits_match.group("section")):
            description = re.sub(r"\s+", " ", match.group(2)).strip()
            description = re.sub(
                CARDHOLDER_LINE,
                "",
                description,
                flags=re.IGNORECASE,
            )
            description = re.sub(r"\s+", " ", description).strip()
            if description.upper().startswith("PAYPAL PURCHASE "):
                description = description.replace("PAYPAL PURCHASE", "PAYPAL OTHER CREDIT", 1)
            rows.append(
                {
                    "Date": expand_statement_mmdd(match.group(1), closing_date),
                    "Type": description,
                    "Subtype": "",
                    "Merchant": clean_paypal_credit_description(description),
                    "Original Input Name": description,
                    "Amount": f"-{match.group(3).replace(',', '')}",
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Other Credits",
                }
            )

    purchases_match = re.search(
        r"Purchases and other debits\s+\$[\d,]+\.\d{2}(?P<section>.+)$",
        normalized_details,
        flags=re.IGNORECASE,
    )
    if purchases_match:
        purchases_section = purchases_match.group("section")
        start_pattern = re.compile(r"(\d{2}/\d{2})\s+[A-Z0-9]+")
        start_matches = list(start_pattern.finditer(purchases_section))

        for index, match in enumerate(start_matches):
            segment_end = start_matches[index + 1].start() if index + 1 < len(start_matches) else len(purchases_section)
            segment = purchases_section[match.start():segment_end].strip()

            lead_match = re.match(r"^(\d{2}/\d{2})\s+[A-Z0-9]+\s+(?P<body>.+)$", segment)
            if not lead_match:
                continue

            body = lead_match.group("body")
            amount_match = re.search(r"\$([\d,]+\.\d{2})", body)
            if not amount_match:
                continue

            prefix = body[:amount_match.start()].strip()
            suffix = body[amount_match.end():]
            suffix = re.sub(r"NO INTEREST IF PAID IN FULL", "", suffix, flags=re.IGNORECASE)
            suffix = re.sub(CARDHOLDER_LINE, "", suffix, flags=re.IGNORECASE)
            suffix = re.sub(r"TOTAL\s+\$[\d,]+\.\d{2}", "", suffix, flags=re.IGNORECASE)
            suffix = re.sub(r"\s+", " ", suffix).strip()

            description = " ".join(part for part in [prefix, suffix] if part).strip()
            merchant = clean_paypal_credit_description(description)
            rows.append(
                {
                    "Date": expand_statement_mmdd(match.group(1), closing_date),
                    "Type": description,
                    "Subtype": "",
                    "Merchant": merchant,
                    "Original Input Name": description,
                    "Amount": amount_match.group(1).replace(",", ""),
                    "Spender": "",
                    "Card": resolved_card_name,
                    "Split": "1.0",
                    "Member B Spend": "",
                    "Member A Spend": "",
                    "Section": "Purchases and Adjustments",
                }
            )

    return rows


def parse_pdf_rows(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> list[dict]:
    """Route a PDF statement to the correct parser."""
    file_path = Path(file_path)
    reader = PdfReader(str(file_path))
    preview_text = "\n".join(
        (page.extract_text() or "")[:4000]
        for page in reader.pages[:2]
    ).lower()

    if "bank of america" in preview_text:
        return parse_bofa_pdf_rows(file_path, card_name=card_name, spender_name=spender_name)

    if "discover it card ending" in preview_text:
        return parse_discover_pdf_rows(file_path, card_name=card_name, spender_name=spender_name)

    if "penfed" in preview_text:
        return parse_penfed_pdf_rows(file_path, card_name=card_name, spender_name=spender_name)

    if "paypal credit" in preview_text or "paypal credit/syncb" in preview_text:
        return parse_paypal_credit_pdf_rows(file_path, card_name=card_name, spender_name=spender_name)

    raise ValueError(f"Unsupported PDF statement format: {file_path.name}")


def summarize_input_file(
    file_path: Path | str,
    card_name: str | None = None,
    spender_name: str | None = None,
    sheet_name: str | int = 0,
) -> dict[str, int]:
    """Summarize skipped non-expense rows for upload reporting."""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        rows = parse_pdf_rows(file_path, card_name=card_name, spender_name=spender_name)
        payments_or_credits_skipped = 0
        for row_dict in rows:
            expense_date = parse_date(row_dict.get('Date'))
            amount = parse_amount(row_dict.get('Amount'))
            if expense_date is None or amount is None:
                continue
            if amount == 0:
                continue

            type_value = str(row_dict.get('Type', '')).strip()
            if 'PAYMENT' in type_value.upper() and amount < 0:
                payments_or_credits_skipped += 1
                continue
            if 'STATEMENT CREDIT' in type_value.upper():
                payments_or_credits_skipped += 1
                continue

        return {
            "payments_or_credits_skipped": payments_or_credits_skipped,
        }

    if suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str, keep_default_na=False)

    csv_format = detect_csv_format(df)

    card_name = infer_statement_card_name(file_path, card_name, spender_name)

    payments_or_credits_skipped = 0

    for _, row in df.iterrows():
        row_dict = row.to_dict()

        if csv_format == 'discover':
            row_dict = normalize_discover_row(row_dict, card_name)
        elif csv_format == 'amex':
            row_dict = normalize_amex_row(row_dict, card_name)
        elif csv_format == 'generic_bank':
            row_dict = normalize_generic_bank_row(row_dict, card_name)

        if csv_format == 'household' and is_malformed_household_tail_row(row_dict):
            continue

        expense_date = parse_date(row_dict.get('Date'))
        amount = parse_amount(row_dict.get('Amount'))
        if expense_date is None or amount is None:
            continue

        type_value = str(row_dict.get('Type', '')).strip()
        if 'PAYMENT' in type_value.upper() and amount < 0:
            payments_or_credits_skipped += 1
            continue
        if 'STATEMENT CREDIT' in type_value.upper():
            payments_or_credits_skipped += 1
            continue

    return {
        "payments_or_credits_skipped": payments_or_credits_skipped,
    }


def read_expenses_csv(
    file_path: Path | str,
    source_file: SourceFile | None = None,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> Generator[Expense, None, None]:
    """Read expenses from a CSV file.

    Args:
        file_path: Path to the CSV file.
        source_file: Optional SourceFile record to link expenses to.
        card_name: Optional card name (e.g., "Discover", "Amex"). If not provided, auto-detected from filename.

    Yields:
        Expense objects parsed from the CSV.
    """
    file_path = Path(file_path)

    # Read CSV with pandas
    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)

    # Detect the format
    csv_format = detect_csv_format(df)
    print(f"  Detected CSV format: {csv_format}")

    # Use provided card name or detect from filename
    card_name = infer_statement_card_name(file_path, card_name, spender_name)
    print(f"  Using card name: {card_name}")

    for _, row in df.iterrows():
        row_dict = row.to_dict()

        # Normalize based on format
        if csv_format == 'discover':
            row_dict = normalize_discover_row(row_dict, card_name)
        elif csv_format == 'amex':
            row_dict = normalize_amex_row(row_dict, card_name)
        elif csv_format == 'generic_bank':
            row_dict = normalize_generic_bank_row(row_dict, card_name)
        # 'household' format is already in the expected format

        if csv_format == 'household' and is_malformed_household_tail_row(row_dict):
            continue

        # Parse date
        expense_date = parse_date(row_dict.get('Date'))
        if expense_date is None:
            continue  # Skip rows without valid dates

        # Parse amount
        amount = parse_amount(row_dict.get('Amount'))
        if amount is None:
            continue  # Skip rows without valid amounts

        # Skip payments and credits (negative amounts that are payments)
        type_value = str(row_dict.get('Type', '')).strip()
        if 'PAYMENT' in type_value.upper() and amount < 0:
            continue
        if 'STATEMENT CREDIT' in type_value.upper():
            continue

        # Keep legitimate negative transactions negative so refunds/reversals
        # are represented correctly in review and reporting. Payments and
        # statement credits were already filtered above.

        subtype_value = str(row_dict.get('Subtype', '')).strip()

        # The household spreadsheet is treated as canonical historical data.
        needs_validation = False if csv_format == 'household' else is_uncategorized_bank_transaction(type_value)
        is_approved = csv_format == 'household'

        # Create expense object
        row_spender = str(row_dict.get('Spender', '')).strip() or None

        expense = Expense(
            date=expense_date.date(),
            type=type_value,
            subtype=subtype_value if subtype_value else None,
            merchant=str(row_dict.get('Merchant', '')).strip() or None,
            original_input_name=str(
                row_dict.get('Original Input Name', row_dict.get('Merchant', ''))
            ).strip() or None,
            amount=amount,
            split=parse_split(row_dict.get('Split')),
            member_b_spend=spend_column(row_dict, 'b'),
            member_a_spend=spend_column(row_dict, 'a'),
            spender=spender_name or row_spender,
            card=str(row_dict.get('Card', '')).strip() or None,
            log_to_work=parse_bool(row_dict.get('Log_to_work')),
            tag=str(row_dict.get('Tag', '')).strip() or None,
            link=str(row_dict.get('Link', '')).strip() or None,
            track_with_friends=str(row_dict.get('Track with Friends', '')).strip() or None,
            source_file=source_file,
            needs_validation=needs_validation,
            is_approved=is_approved,
            content_hash=compute_content_hash(row_dict),
        )

        yield expense


def read_expenses_excel(
    file_path: Path | str,
    source_file: SourceFile | None = None,
    card_name: str | None = None,
    spender_name: str | None = None,
    sheet_name: str | int = 0,
) -> Generator[Expense, None, None]:
    """Read expenses from an Excel file.

    Args:
        file_path: Path to the Excel file.
        source_file: Optional SourceFile record to link expenses to.
        card_name: Optional card name (e.g., "Discover", "Amex"). If not provided, auto-detected from filename.
        sheet_name: Name or index of the sheet to read.

    Yields:
        Expense objects parsed from the Excel file.
    """
    file_path = Path(file_path)

    # Read Excel with pandas
    df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str, keep_default_na=False)

    # Detect the format
    csv_format = detect_csv_format(df)
    print(f"  Detected Excel format: {csv_format}")

    # Use provided card name or detect from filename
    card_name = infer_statement_card_name(file_path, card_name, spender_name)
    print(f"  Using card name: {card_name}")

    for _, row in df.iterrows():
        row_dict = row.to_dict()

        # Normalize based on format
        if csv_format == 'discover':
            row_dict = normalize_discover_row(row_dict, card_name)
        elif csv_format == 'amex':
            row_dict = normalize_amex_row(row_dict, card_name)
        elif csv_format == 'generic_bank':
            row_dict = normalize_generic_bank_row(row_dict, card_name)

        if csv_format == 'household' and is_malformed_household_tail_row(row_dict):
            continue

        expense_date = parse_date(row_dict.get('Date'))
        if expense_date is None:
            continue

        amount = parse_amount(row_dict.get('Amount'))
        if amount is None:
            continue

        # Skip payments
        type_value = str(row_dict.get('Type', '')).strip()
        if 'PAYMENT' in type_value.upper() and amount < 0:
            continue
        if 'STATEMENT CREDIT' in type_value.upper():
            continue

        # Keep legitimate negative transactions negative so refunds/reversals
        # are represented correctly in review and reporting. Payments and
        # statement credits were already filtered above.

        subtype_value = str(row_dict.get('Subtype', '')).strip()
        needs_validation = False if csv_format == 'household' else is_uncategorized_bank_transaction(type_value)
        is_approved = csv_format == 'household'

        row_spender = str(row_dict.get('Spender', '')).strip() or None

        expense = Expense(
            date=expense_date.date(),
            type=type_value,
            subtype=subtype_value if subtype_value else None,
            merchant=str(row_dict.get('Merchant', '')).strip() or None,
            original_input_name=str(
                row_dict.get('Original Input Name', row_dict.get('Merchant', ''))
            ).strip() or None,
            amount=amount,
            split=parse_split(row_dict.get('Split')),
            member_b_spend=spend_column(row_dict, 'b'),
            member_a_spend=spend_column(row_dict, 'a'),
            spender=spender_name or row_spender,
            card=str(row_dict.get('Card', '')).strip() or None,
            log_to_work=parse_bool(row_dict.get('Log_to_work')),
            tag=str(row_dict.get('Tag', '')).strip() or None,
            link=str(row_dict.get('Link', '')).strip() or None,
            track_with_friends=str(row_dict.get('Track with Friends', '')).strip() or None,
            source_file=source_file,
            needs_validation=needs_validation,
            is_approved=is_approved,
            content_hash=compute_content_hash(row_dict),
        )

        yield expense


def read_expenses_pdf(
    file_path: Path | str,
    source_file: SourceFile | None = None,
    card_name: str | None = None,
    spender_name: str | None = None,
) -> Generator[Expense, None, None]:
    """Read expenses from a supported PDF statement."""
    file_path = Path(file_path)
    card_name = infer_statement_card_name(file_path, card_name, spender_name)
    print("  Detected PDF format: supported_statement")
    print(f"  Using card name: {card_name}")

    for row_dict in parse_pdf_rows(file_path, card_name=card_name, spender_name=spender_name):
        expense_date = parse_date(row_dict.get('Date'))
        if expense_date is None:
            continue

        amount = parse_amount(row_dict.get('Amount'))
        if amount is None:
            continue
        if amount == 0:
            continue

        type_value = str(row_dict.get('Type', '')).strip()
        if 'PAYMENT' in type_value.upper() and amount < 0:
            continue
        if 'STATEMENT CREDIT' in type_value.upper():
            continue

        expense = Expense(
            date=expense_date.date(),
            type=type_value,
            subtype=None,
            merchant=str(row_dict.get('Merchant', '')).strip() or None,
            original_input_name=str(
                row_dict.get('Original Input Name', row_dict.get('Merchant', ''))
            ).strip() or None,
            amount=amount,
            split=Decimal("1.0"),
            member_b_spend=None,
            member_a_spend=None,
            spender=spender_name or None,
            card=str(row_dict.get('Card', '')).strip() or None,
            log_to_work=None,
            tag=None,
            link=None,
            track_with_friends=None,
            source_file=source_file,
            needs_validation=is_uncategorized_bank_transaction(type_value),
            is_approved=False,
            content_hash=compute_content_hash(row_dict),
        )

        yield expense
