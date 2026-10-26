"""Rule-based expense categorization with validation queue."""

import re
from typing import Tuple

from sqlalchemy.orm import Session

from ..household import household_sheet
from ..db import Category, Subtype, Expense, MerchantMapping, ValidationQueueItem


# Merchants that require manual validation (too vague for auto-categorization)
VAGUE_MERCHANTS = {
    'amazon': 'Could be Electronics, Household, Clothing, etc.',
    'ebay': 'Marketplace - category depends on purchase',
    'etsy': 'Crafts, gifts, clothing, etc.',
    'target': 'Groceries, household, clothing, etc.',
    'walmart': 'Groceries, household, clothing, etc.',
    'costco': 'Groceries, household, electronics, etc.',
    'paypal': 'Underlying merchant unknown',
    'venmo': 'Person-to-person - category unclear',
    'zelle': 'Person-to-person - category unclear',
    'doordash': 'Restaurant depends on order',
    'uber_eats': 'Restaurant depends on order',
    'grubhub': 'Restaurant depends on order',
    'instacart': 'Grocery store depends on items',
}

# Default category mappings for common merchants
DEFAULT_MERCHANT_MAPPINGS = {
    # Cafes and Coffee
    r'starbucks': ('Food and Drink - Dining Out', 'Cafe'),
    r'dunkin': ('Food and Drink - Dining Out', 'Cafe'),
    r'peet.*coffee': ('Food and Drink - Dining Out', 'Cafe'),
    r'blue.?bottle': ('Food and Drink - Dining Out', 'Cafe'),

    # Fast Food
    r'mcdonalds?': ('Food and Drink - Dining Out', 'Fast Food'),
    r'burger.?king': ('Food and Drink - Dining Out', 'Fast Food'),
    r'wendys?': ('Food and Drink - Dining Out', 'Fast Food'),
    r'chipotle': ('Food and Drink - Dining Out', 'Fast Food'),
    r'taco.?bell': ('Food and Drink - Dining Out', 'Fast Food'),
    r'chick.?fil.?a': ('Food and Drink - Dining Out', 'Fast Food'),

    # Grocery Stores
    r'whole.?foods': ('Food and Drink - Grocery', 'Grocery Store'),
    r'trader.?joe': ('Food and Drink - Grocery', 'Grocery Store'),
    r'market.?basket': ('Food and Drink - Grocery', 'Grocery Store'),
    r'shaws?': ('Food and Drink - Grocery', 'Grocery Store'),
    r'stop.?shop': ('Food and Drink - Grocery', 'Grocery Store'),
    r'kroger': ('Food and Drink - Grocery', 'Grocery Store'),
    r'safeway': ('Food and Drink - Grocery', 'Grocery Store'),
    r'aldi': ('Food and Drink - Grocery', 'Grocery Store'),

    # Gas Stations
    r'exxon': ('Travel', 'Gasoline'),
    r'mobil': ('Travel', 'Gasoline'),
    r'shell': ('Travel', 'Gasoline'),
    r'chevron': ('Travel', 'Gasoline'),
    r'bp\s': ('Travel', 'Gasoline'),
    r'76\s': ('Travel', 'Gasoline'),
    r'loves': ('Travel', 'Gasoline'),

    # Ride Sharing
    r'uber(?!.?eats)': ('Travel', 'Ride Sharing'),
    r'lyft': ('Travel', 'Ride Sharing'),

    # Subscriptions
    r'netflix': ('Subscriptions', 'Streaming'),
    r'spotify': ('Subscriptions', 'Music'),
    r'apple.?music': ('Subscriptions', 'Music'),
    r'youtube.?premium': ('Subscriptions', 'Youtube Membership'),
    r'hbo.?max': ('Subscriptions', 'Streaming'),
    r'disney.?\+': ('Subscriptions', 'Streaming'),
    r'hulu': ('Subscriptions', 'Streaming'),
    r'amazon.?prime': ('Subscriptions', 'Free Shipping'),
    r'openai|chatgpt': ('Subscriptions', 'Generative AI'),

    # Pharmacies
    r'cvs': ('Medical Supplies', 'Miscellaneous'),
    r'walgreens': ('Medical Supplies', 'Miscellaneous'),
    r'rite.?aid': ('Medical Supplies', 'Miscellaneous'),

    # Home Improvement
    r'home.?depot': ('Home Improvement', 'Miscellaneous'),
    r'lowes?': ('Home Improvement', 'Miscellaneous'),
    r'ace.?hardware': ('Home Improvement', 'Tools'),
    r'harbor.?freight': ('Home Improvement', 'Tools'),

    # Electronics (specific)
    r'best.?buy': ('Electronics', 'Miscellaneous'),
    r'apple.?store': ('Electronics', 'Cellular Phone'),

    # Travel
    r'united.?airlines?': ('Travel', 'Airfare'),
    r'delta.?air': ('Travel', 'Airfare'),
    r'american.?airlines?': ('Travel', 'Airfare'),
    r'southwest': ('Travel', 'Airfare'),
    r'jetblue': ('Travel', 'Airfare'),
    r'airbnb': ('Travel', 'Lodging'),
    r'marriott': ('Travel', 'Hotel'),
    r'hilton': ('Travel', 'Hotel'),
    r'hyatt': ('Travel', 'Hotel'),
}


def is_vague_merchant(merchant: str) -> Tuple[bool, str | None]:
    """Check if a merchant is too vague for auto-categorization.

    Returns:
        Tuple of (is_vague, reason)
    """
    if not merchant:
        return False, None

    merchant_lower = merchant.lower().replace('_', ' ')

    for pattern, reason in VAGUE_MERCHANTS.items():
        if pattern in merchant_lower:
            return True, reason

    return False, None


def find_category_match(
    merchant: str,
    session: Session,
) -> Tuple[Category | None, Subtype | None, str | None]:
    """Find a matching category for a merchant.

    First checks database merchant_mappings, then falls back to default patterns.

    Returns:
        Tuple of (Category, Subtype) or (None, None) if no match.
    """
    if not merchant:
        return None, None, None

    merchant_lower = merchant.lower().strip()

    # First check database mappings
    mappings = session.query(MerchantMapping).all()
    for mapping in mappings:
        pattern = (mapping.merchant_pattern or "").strip().lower()
        if not pattern:
            continue

        if (
            pattern == merchant_lower
            or pattern in merchant_lower
            or merchant_lower in pattern
        ):
            category = session.query(Category).filter_by(id=mapping.category_id).first()
            subtype = session.query(Subtype).filter_by(id=mapping.subtype_id).first() if mapping.subtype_id else None
            return category, subtype, mapping.canonical_merchant

        if re.search(mapping.merchant_pattern, merchant_lower, re.IGNORECASE):
            category = session.query(Category).filter_by(id=mapping.category_id).first()
            subtype = session.query(Subtype).filter_by(id=mapping.subtype_id).first() if mapping.subtype_id else None
            return category, subtype, mapping.canonical_merchant

    # Fall back to default patterns
    for pattern, (cat_name, subtype_name) in DEFAULT_MERCHANT_MAPPINGS.items():
        if re.search(pattern, merchant_lower, re.IGNORECASE):
            category = session.query(Category).filter_by(name=cat_name).first()
            subtype = None
            if category and subtype_name:
                subtype = session.query(Subtype).filter(
                    Subtype.category_id == category.id,
                    Subtype.name == subtype_name
                ).first()
            return category, subtype, None

    return None, None, None


def find_existing_category_assignment(
    expense: Expense,
    session: Session,
) -> Tuple[Category | None, Subtype | None]:
    """Resolve canonical category/subtype names already stored on an expense.

    Household spreadsheet imports already contain trusted category labels in
    `expense.type` and `expense.subtype`. Prefer linking those records directly
    before applying vague-merchant heuristics.
    """
    if not expense.type:
        return None, None

    category = session.query(Category).filter_by(name=expense.type).first()
    if not category:
        return None, None

    subtype = None
    if expense.subtype:
        subtype = session.query(Subtype).filter(
            Subtype.category_id == category.id,
            Subtype.name == expense.subtype
        ).first()

    return category, subtype


def is_trusted_household_import(expense: Expense) -> bool:
    """Return True when an expense came from the canonical household spreadsheet."""
    if not expense.source_file or not expense.source_file.filename:
        return False

    return expense.source_file.filename == household_sheet()


def categorize_expense(
    expense: Expense,
    session: Session,
    auto_flag_vague: bool = True,
) -> bool:
    """Categorize an expense based on merchant patterns.

    Args:
        expense: The expense to categorize.
        session: Database session.
        auto_flag_vague: If True, flag vague merchants for validation.

    Returns:
        True if successfully categorized, False if needs validation.
    """
    # Skip if already categorized (has type that's a canonical category)
    if expense.category_id is not None:
        return True

    # Household imports already carry trusted canonical category labels.
    category, subtype = find_existing_category_assignment(expense, session)
    if category:
        expense.category_id = category.id
        expense.category_rel = category
        expense.needs_validation = False
        expense.is_approved = True
        if subtype:
            expense.subtype_id = subtype.id
            expense.subtype_rel = subtype
        return True

    if is_trusted_household_import(expense):
        expense.needs_validation = False
        expense.is_approved = True
        return True

    merchant = expense.merchant or ''

    # Learned mappings should win before vague-merchant heuristics so remembered
    # merchant/category pairs actually apply on future imports.
    category, subtype, canonical_merchant = find_category_match(merchant, session)
    if category:
        expense.category_id = category.id
        expense.category_rel = category
        expense.needs_validation = False
        if subtype:
            expense.subtype_id = subtype.id
            expense.subtype_rel = subtype
        if canonical_merchant:
            expense.merchant = canonical_merchant
        return True

    # Check if it's a vague merchant
    if auto_flag_vague:
        is_vague, reason = is_vague_merchant(merchant)
        if is_vague:
            expense.needs_validation = True
            # Create validation queue item
            existing = session.query(ValidationQueueItem).filter_by(expense_id=expense.id).first()
            if not existing and expense.id:
                queue_item = ValidationQueueItem(
                    expense_id=expense.id,
                    reason='vague_merchant',
                    status='pending',
                )
                session.add(queue_item)
            return False

    # No match found - flag for validation if it's an uncategorized bank transaction
    if expense.needs_validation:
        existing = session.query(ValidationQueueItem).filter_by(expense_id=expense.id).first()
        if not existing and expense.id:
            queue_item = ValidationQueueItem(
                expense_id=expense.id,
                reason='new_merchant',
                status='pending',
            )
            session.add(queue_item)
        return False

    return True  # Already has a type from CSV, just not linked to Category model


def categorize_expenses_batch(
    expenses: list[Expense],
    session: Session,
    auto_flag_vague: bool = True,
) -> dict:
    """Categorize a batch of expenses.

    Args:
        expenses: List of expenses to categorize.
        session: Database session.
        auto_flag_vague: If True, flag vague merchants for validation.

    Returns:
        Dictionary with categorization statistics.
    """
    stats = {
        'total': len(expenses),
        'categorized': 0,
        'needs_validation': 0,
        'skipped': 0,
    }

    for expense in expenses:
        if categorize_expense(expense, session, auto_flag_vague):
            stats['categorized'] += 1
        else:
            stats['needs_validation'] += 1

    return stats


def get_pending_validations(session: Session, limit: int = 50) -> list[ValidationQueueItem]:
    """Get pending validation queue items.

    Args:
        session: Database session.
        limit: Maximum number of items to return.

    Returns:
        List of ValidationQueueItem objects with pending status.
    """
    return (
        session.query(ValidationQueueItem)
        .filter_by(status='pending')
        .limit(limit)
        .all()
    )


def validate_expense(
    expense_id: int,
    category_id: int,
    subtype_id: int | None,
    session: Session,
    remember_mapping: bool = False,
    canonical_merchant: str | None = None,
) -> bool:
    """Validate an expense's category.

    Args:
        expense_id: ID of the expense to validate.
        category_id: ID of the validated category.
        subtype_id: ID of the validated subtype (optional).
        session: Database session.
        remember_mapping: If True, create a merchant mapping for future auto-categorization.

    Returns:
        True if successful.
    """
    expense = session.query(Expense).filter_by(id=expense_id).first()
    if not expense:
        return False

    # Update expense
    expense.category_id = category_id
    expense.subtype_id = subtype_id
    expense.needs_validation = False
    expense.is_approved = True
    from datetime import datetime
    expense.approved_at = datetime.now()
    if canonical_merchant:
        expense.merchant = canonical_merchant

    # Update validation queue item
    queue_item = session.query(ValidationQueueItem).filter_by(expense_id=expense_id).first()
    if queue_item:
        queue_item.status = 'validated'
        queue_item.validated_at = datetime.now()

    # Create merchant mapping if requested
    if remember_mapping and expense.merchant:
        existing_mapping = session.query(MerchantMapping).filter_by(
            merchant_pattern=(expense.original_input_name or expense.merchant).lower()
        ).first()

        if not existing_mapping:
            mapping = MerchantMapping(
                merchant_pattern=(expense.original_input_name or expense.merchant).lower(),
                category_id=category_id,
                subtype_id=subtype_id,
                canonical_merchant=canonical_merchant or expense.merchant,
                requires_validation=False,
            )
            session.add(mapping)

    return True
