# Validation modules
from .duplicate_detector import (
    find_exact_duplicates,
    find_fuzzy_duplicates,
    check_and_mark_duplicate,
    find_all_duplicates_in_batch,
    get_duplicate_groups,
    merge_duplicates,
)

__all__ = [
    "find_exact_duplicates",
    "find_fuzzy_duplicates",
    "check_and_mark_duplicate",
    "find_all_duplicates_in_batch",
    "get_duplicate_groups",
    "merge_duplicates",
]
