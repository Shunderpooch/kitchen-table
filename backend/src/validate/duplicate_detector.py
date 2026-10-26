"""Duplicate detection for expenses using hash-based and fuzzy matching."""

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Tuple

from rapidfuzz import fuzz
from sqlalchemy.orm import Session
from sqlalchemy.orm import load_only

from ..db import Expense


# Thresholds for fuzzy matching
MERCHANT_SIMILARITY_THRESHOLD = 85  # Percentage similarity for merchant names
DATE_WINDOW_DAYS = 3  # Days within which to consider potential duplicates
AMOUNT_TOLERANCE = Decimal("0.01")  # Tolerance for amount matching


def find_exact_duplicates(
    expense: Expense,
    session: Session,
) -> List[Expense]:
    """Find exact duplicates based on content hash.

    Args:
        expense: The expense to check for duplicates.
        session: Database session.

    Returns:
        List of duplicate expenses (matching content_hash).
    """
    if not expense.content_hash:
        return []

    duplicates = (
        session.query(Expense)
        .options(load_only(Expense.id))
        .filter(
            Expense.content_hash == expense.content_hash,
            Expense.id != expense.id if expense.id else True,
        )
        .all()
    )

    return duplicates


def find_fuzzy_duplicates(
    expense: Expense,
    session: Session,
    date_window: int = DATE_WINDOW_DAYS,
    merchant_threshold: int = MERCHANT_SIMILARITY_THRESHOLD,
    amount_tolerance: Decimal = AMOUNT_TOLERANCE,
) -> List[Tuple[Expense, float]]:
    """Find potential duplicates using fuzzy matching.

    Looks for expenses with:
    - Same or very similar amount (within tolerance)
    - Date within window
    - Similar merchant name

    Args:
        expense: The expense to check for duplicates.
        session: Database session.
        date_window: Days before/after to search.
        merchant_threshold: Minimum similarity percentage for merchant names.
        amount_tolerance: Maximum difference in amounts to consider.

    Returns:
        List of tuples (duplicate_expense, similarity_score).
    """
    if not expense.date or not expense.amount:
        return []

    # Calculate date range
    start_date = expense.date - timedelta(days=date_window)
    end_date = expense.date + timedelta(days=date_window)

    # Query potential duplicates
    candidates = (
        session.query(Expense)
        .options(load_only(Expense.id, Expense.date, Expense.amount, Expense.merchant))
        .filter(
            Expense.date >= start_date,
            Expense.date <= end_date,
            Expense.id != expense.id if expense.id else True,
        )
        .all()
    )

    duplicates = []

    for candidate in candidates:
        # Check amount similarity
        if candidate.amount is None:
            continue

        amount_diff = abs(candidate.amount - expense.amount)
        if amount_diff > amount_tolerance:
            continue

        # Check merchant similarity
        if expense.merchant and candidate.merchant:
            similarity = fuzz.ratio(
                expense.merchant.lower(),
                candidate.merchant.lower()
            )
            if similarity >= merchant_threshold:
                # Score based on amount match (100%), date proximity, and merchant similarity
                date_diff = abs((expense.date - candidate.date).days)
                date_score = 100 - (date_diff * 10)  # Lose 10 points per day difference
                overall_score = (similarity + date_score + 100) / 3  # Average of three scores

                duplicates.append((candidate, overall_score))
        elif not expense.merchant and not candidate.merchant:
            # Both have no merchant - check if dates and amounts match exactly
            if amount_diff == 0 and expense.date == candidate.date:
                duplicates.append((candidate, 95.0))

    # Sort by similarity score descending
    duplicates.sort(key=lambda x: x[1], reverse=True)

    return duplicates


def check_and_mark_duplicate(
    expense: Expense,
    session: Session,
    use_fuzzy: bool = True,
) -> bool:
    """Check if an expense is a duplicate and mark it if so.

    Args:
        expense: The expense to check.
        session: Database session.
        use_fuzzy: Whether to use fuzzy matching (in addition to exact).

    Returns:
        True if the expense is a duplicate.
    """
    # First check exact duplicates
    exact_matches = find_exact_duplicates(expense, session)
    if exact_matches:
        expense.is_duplicate = True
        return True

    # Then check fuzzy duplicates
    if use_fuzzy:
        fuzzy_matches = find_fuzzy_duplicates(expense, session)
        if fuzzy_matches and fuzzy_matches[0][1] >= 95:  # High confidence threshold
            expense.is_duplicate = True
            return True

    return False


def find_all_duplicates_in_batch(
    expenses: List[Expense],
    session: Session,
) -> dict:
    """Find all duplicates in a batch of expenses.

    Checks both against existing database records and within the batch itself.

    Args:
        expenses: List of expenses to check.
        session: Database session.

    Returns:
        Dictionary with duplicate detection statistics and results.
    """
    results = {
        'total': len(expenses),
        'exact_duplicates': 0,
        'fuzzy_duplicates': 0,
        'unique': 0,
        'duplicates': [],  # List of (expense, duplicate_of) tuples
    }

    # Create hash map for batch-internal duplicate detection
    hash_map = {}

    for expense in expenses:
        # Check for duplicates in database
        is_dup = check_and_mark_duplicate(expense, session)

        if is_dup:
            results['exact_duplicates'] += 1
            continue

        # Check for duplicates within batch
        if expense.content_hash:
            if expense.content_hash in hash_map:
                expense.is_duplicate = True
                results['exact_duplicates'] += 1
                results['duplicates'].append((expense, hash_map[expense.content_hash]))
            else:
                hash_map[expense.content_hash] = expense
                results['unique'] += 1
        else:
            results['unique'] += 1

    return results


def get_duplicate_groups(session: Session) -> List[List[Expense]]:
    """Get groups of duplicate expenses.

    Returns:
        List of lists, where each inner list contains expenses that are duplicates of each other.
    """
    # Get all expenses with duplicate content hashes
    from sqlalchemy import func

    duplicate_hashes = (
        session.query(Expense.content_hash)
        .filter(Expense.content_hash.isnot(None))
        .group_by(Expense.content_hash)
        .having(func.count(Expense.id) > 1)
        .all()
    )

    groups = []
    for (hash_value,) in duplicate_hashes:
        group = (
            session.query(Expense)
            .filter(Expense.content_hash == hash_value)
            .order_by(Expense.created_at)
            .all()
        )
        groups.append(group)

    return groups


def merge_duplicates(
    keep_id: int,
    remove_ids: List[int],
    session: Session,
) -> bool:
    """Merge duplicate expenses by keeping one and removing others.

    Args:
        keep_id: ID of the expense to keep.
        remove_ids: IDs of expenses to remove.
        session: Database session.

    Returns:
        True if successful.
    """
    keep_expense = session.query(Expense).filter_by(id=keep_id).first()
    if not keep_expense:
        return False

    for remove_id in remove_ids:
        expense = session.query(Expense).filter_by(id=remove_id).first()
        if expense:
            session.delete(expense)

    return True
