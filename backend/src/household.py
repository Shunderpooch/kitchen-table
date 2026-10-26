"""Who the household is: one or two people, from configuration.

``HOUSEHOLD_MEMBERS=Alex,Sam`` (environment, or ``frontend/.env``, which the
app and the scheduled tasks share). The first person is member A, the
second member B; spending is split between them (``member_a_spend``,
``member_b_spend``). Statement sources are named "<person> <card>", e.g.
"Alex Amex", so the person who owns a card is the first word of its name.

``SELF_NAME=alex,smith`` (first, last): the owner's own name, so scanned
documents that mention it are not mistaken for someone else's.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMBERS = ("Alex", "Sam")


def _setting(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value:
        return value
    env_file = REPO_ROOT / "frontend" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, rest = line.partition("=")
            if key.strip() == name and rest.strip():
                return rest.strip().strip('"').strip("'")
    return None


@lru_cache(maxsize=1)
def members() -> tuple[str, ...]:
    """The household's people, first one first (one or two names)."""
    raw = _setting("HOUSEHOLD_MEMBERS")
    names = tuple(n.strip() for n in raw.split(",") if n.strip())[:2] if raw else DEFAULT_MEMBERS
    return names or DEFAULT_MEMBERS


def member_a() -> str:
    return members()[0]


def member_b() -> Optional[str]:
    return members()[1] if len(members()) > 1 else None


def member_for(text: Optional[str]) -> Optional[str]:
    """The member a card/source name or people-folder belongs to ("Sam Amex",
    "smith_sam" -> "Sam"), matching first names case-insensitively."""
    if not text:
        return None
    words = {w for w in text.lower().replace("_", " ").split()}
    return next((m for m in members() if m.lower() in words), None)


def household_sheet() -> str:
    """Filename of the household spreadsheet export treated as canonical history
    (``HOUSEHOLD_SHEET``)."""
    return _setting("HOUSEHOLD_SHEET") or "Household Expenses.csv"


def statement_sources() -> list[tuple[str, str]]:
    """Statement sources to start with: each member's cards (``STATEMENT_CARDS``,
    default "Amex,Discover,Visa") and bank accounts (``BANK_ACCOUNTS``, default
    "Checking"), named "<member> <card>"."""
    cards = [c.strip() for c in (_setting("STATEMENT_CARDS") or "Amex,Discover,Visa").split(",") if c.strip()]
    accounts = [a.strip() for a in (_setting("BANK_ACCOUNTS") or "Checking").split(",") if a.strip()]
    return ([(f"{m} {c}", "credit_card") for m in members() for c in cards]
            + [(f"{m} {a}", "bank_account") for m in members() for a in accounts])


@lru_cache(maxsize=1)
def self_name() -> tuple[str, ...]:
    """The owner's (first, last) name in lower case, for reading scanned documents."""
    raw = _setting("SELF_NAME")
    if raw:
        return tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return (member_a().lower(),)
