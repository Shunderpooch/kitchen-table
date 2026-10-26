"""What a receipt says about how it was paid.

From the OCR text of a filed receipt: every money amount printed on it, the
amount actually charged (total due, amount paid, the card line), the last
four digits of the card, how it was paid (card, cash, gift card or app
balance), and whether it came through a delivery app (the statement then
names the app, not the restaurant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .naming import slugify

_MONEY = r"\$?\s?(\d{1,3}(?:,\d{3})*|\d+)[.,](\d{2})(?!\d)"
_MONEY_RE = re.compile(r"(?<![\d.])" + _MONEY)
# Labels that introduce the amount paid, strongest first. OCR misreads are
# allowed for "total" (TOTAI, T0TAL, Tota!).
_PAID_LABELS = [
    ("paid", r"amount\s*paid|total\s*paid|amount\s*charged|total\s*charged|charged|payment\s*amount|purchase\s*amount|you\s*paid|total\s*payments?"),
    ("due", r"grand\s*t[o0]ta[l1i!]|t[o0]ta[l1i!]\s*due|amount\s*due|balance\s*due|order\s*t[o0]ta[l1i!]"),
    ("total", r"(?<!sub)(?<!sub\s)t[o0]ta[l1i!](?!\s*(?:savings|items?|tax|discount|qty|number|count|tip))"),
    ("card", r"visa|amex|american\s*express|discover|master\s*card|mastercard|\bmc\b|debit|credit|chip|contactless"),
]
_PAID_RES = [(name, re.compile(rf"(?:{pattern})[^\d\n$]{{0,24}}" + _MONEY, re.I)) for name, pattern in _PAID_LABELS]
# "Suggested tip: 20% (Tip $13.56 Total $87.12)" is not what was paid.
_SUGGESTION_RE = re.compile(r"suggest|\d\s?%|tip\s*guide", re.I)
_TIP_RE = re.compile(r"\b(?:tip|gratuity)\b[^\d\n$]{0,12}" + _MONEY, re.I)
_LAST4_RE = re.compile(
    r"(?:[*xX•#]{4,}[\s-]*|ending\s+in\s+|acct[:#\s]*[*xX]*|card\s*(?:#|no\.?|number)?\s*[*xX]+)(\d{4})\b", re.I
)
# Paid from a gift card or app balance: the gift-card line carries a payment
# amount ("Starbucks Gift Card $6.37", "Gift Card Redeem $7.13"). A gift card
# merely mentioned (a footer ad, a gift card bought) does not count.
_GIFT_RE = re.compile(
    r"(?:gift\s*card(?:\s*redeem(?:ed)?)?|e-?gift|starbucks\s*card|\bdd\s*card|stored\s*value|app\s*balance)"
    r"[^\n\d$]{0,16}\$?\s?\d{1,4}[.,]\d{2}",
    re.I,
)
_GIFT_PURCHASE_RE = re.compile(r"(?:branded|activat\w*|new|load|reload)\s*gift\s*card|gift\s*card\s*(?:activation|load|sale)", re.I)
_CASH_RE = re.compile(r"(?<![\w-])cash(?!\s*back)(?:\s*tend(?:er|ered)?)?[^\n\d$]{0,10}\$?\d", re.I)
_PLATFORMS = {
    "uber_eats": re.compile(r"uber\s*eats|ubereats", re.I),
    "doordash": re.compile(r"door\s*dash", re.I),
    "grubhub": re.compile(r"grub\s*hub", re.I),
    "instacart": re.compile(r"instacart", re.I),
    "seamless": re.compile(r"seamless", re.I),
    "postmates": re.compile(r"postmates", re.I),
}
GENERIC_TOKENS = {
    "purchase", "debit", "credit", "card", "visa", "payment", "online", "store", "stores", "inc", "llc", "corp",
    "company", "the", "and", "www", "com", "http", "https", "paypal", "amzn", "mktp", "recurring", "pending",
    "with", "from", "restaurant", "market", "shop", "cafe", "coffee", "food", "grill", "pizza", "bar", "kitchen",
    "service", "services", "center", "north", "south", "east", "west", "street", "main", "usa", "mass", "boston",
}


def _amount(match: re.Match) -> float:
    return float(f"{int(match.group(1).replace(',', ''))}.{match.group(2)}")


@dataclass
class ReceiptFacts:
    amounts: set[str] = field(default_factory=set)
    charged: Optional[float] = None  # best guess at what the card/cash paid
    charged_label: Optional[str] = None
    tip: Optional[float] = None
    last4: set[str] = field(default_factory=set)
    tender: Optional[str] = None  # card / cash / gift
    platform: Optional[str] = None
    squashed: str = ""  # slug of the whole text without separators, for name search


def read_receipt(text: str) -> ReceiptFacts:
    facts = ReceiptFacts()
    facts.amounts = {f"{_amount(m):.2f}" for m in _MONEY_RE.finditer(text)}
    def on_suggestion_line(match: re.Match) -> bool:
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.end())
        return bool(_SUGGESTION_RE.search(text[start:end if end >= 0 else len(text)]))

    for name, pattern in _PAID_RES:
        found = [_amount(m) for m in pattern.finditer(text) if _amount(m) > 0 and not on_suggestion_line(m)]
        if found:
            # Several totals: the last one printed is usually the final charge.
            facts.charged, facts.charged_label = found[-1], name
            break
    tips = [_amount(m) for m in _TIP_RE.finditer(text) if _amount(m) > 0]
    facts.tip = tips[-1] if tips else None
    facts.last4 = set(_LAST4_RE.findall(text))
    card_paid = facts.charged_label == "card"
    if _GIFT_RE.search(text) and not _GIFT_PURCHASE_RE.search(text) and not card_paid:
        facts.tender = "gift"
    elif facts.last4 or re.search(r"\b(visa|amex|american express|discover|mastercard)\b", text, re.I):
        facts.tender = "card"
    elif _CASH_RE.search(text):
        facts.tender = "cash"
    facts.platform = next((name for name, pattern in _PLATFORMS.items() if pattern.search(text)), None)
    facts.squashed = slugify(text).replace("_", "")
    return facts


def platform_of(name: Optional[str]) -> Optional[str]:
    return next((key for key, pattern in _PLATFORMS.items() if name and pattern.search(name.replace("_", " "))), None)


def name_tokens(*names: Optional[str]) -> list[str]:
    """Distinctive words of a statement merchant name, for finding it on a receipt."""
    tokens = []
    for name in names:
        for token in slugify(name or "").split("_"):
            if len(token) >= 4 and not token.isdigit() and token not in GENERIC_TOKENS and token not in tokens:
                tokens.append(token)
    return tokens
