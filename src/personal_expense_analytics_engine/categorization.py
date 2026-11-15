"""Rule-based categorization. Rules are plain data: edit the lists to improve results."""

import re

from personal_expense_analytics_engine.models import CategoryType, TransactionDirection
from personal_expense_analytics_engine.normalization import normalize_text

# "З Білої картки", "На чорну картку": moves between your own cards
OWN_CARD_TRANSFER = re.compile(r"^(з|на)\s+(\S+\s+)?карт")
# "Від: Name": money received from a person
INCOMING_FROM_PERSON = re.compile(r"^від\s")

# A keyword matches when it appears at the START of a word or phrase in the
# normalized text ("mcdonald" matches "mcdonald's"; "нова пошта" matches that phrase).
KEYWORD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Groceries", ("biedronka", "żabka", "zabka", "lidl", "carrefour", "auchan",
                   "kaufland", "silpo", "сільпо", "атб", "novus")),
    ("Dining", ("mcdonald", "kfc", "starbucks", "glovo", "wolt", "pyszne")),
    ("Transport", ("uber", "bolt", "koleo", "pkp", "mpk", "jakdojade", "flixbus",
                   "укрзалізниця")),
    ("Subscriptions", ("netflix", "spotify", "youtube", "icloud", "itunes",
                       "openai", "anthropic", "github", "megogo")),
    ("Utilities", ("вода", "водоканал", "водопостач", "газопостач", "електро",
                   "опалення", "інтернет")),
    ("Delivery", ("нова пошта", "nova poshta", "justin", "inpost", "dpd",
                  "укрпошта", "ukrposhta")),
]

_MCC_GROUPS: dict[str, list[int]] = {
    "Groceries": [5411, 5422, 5441, 5451, 5462, 5499],
    "Dining": [5811, 5812, 5813, 5814],
    "Transport": [4111, 4112, 4121, 4131, 4784, 4789, 5541, 5542, 7523],
    "Utilities": [4900, 4814],
    "Health": [5912, 8011, 8021, 8031, 8041, 8042, 8043, 8049, 8050, 8062, 8071, 8099],
    "Shopping": [5262, 5311, 5331, 5399, 5651, 5661, 5691, 5699, 5732, 5734, 5942,
                 5944, 5945, 5947, 5999],
    "Entertainment": [7832, 7922, 7991, 7994, 7996, 7997, 7999],
    "Subscriptions": [4816, 4899, 5815, 5816, 5817, 5818, 5968, 7841],
    "Travel": [4511, 7011, *range(3000, 3300), *range(3500, 4000)],
    "Cash": [6010, 6011],
    "Education": [8211, 8220, 8241, 8244, 8249, 8299],
    "Personal care": [5977, 7230, 7297, 7298],
    "Pets": [742, 5995],
    "Services": [8999],
    "Transfer": [4829, 6538],
}
MCC_CATEGORIES: dict[int, str] = {
    code: name for name, codes in _MCC_GROUPS.items() for code in codes
}

CATEGORY_TYPES: dict[str, CategoryType] = {
    "Transfer": CategoryType.TRANSFER,
    "Income": CategoryType.INCOME,
    "Utilities": CategoryType.FIXED,
    "Subscriptions": CategoryType.FIXED,
}


def category_type(name: str) -> CategoryType:
    """Return the CategoryType of a category name (VARIABLE unless listed above)."""
    return CATEGORY_TYPES.get(name, CategoryType.VARIABLE)


def categorize(
    description: str,
    *,
    direction: TransactionDirection,
    mcc: int | None = None,
    merchant: str | None = None,
) -> str | None:
    """Return a category name, or None if no rule matches.

    Order: own-card transfer, incoming from a person, keywords, MCC, credit fallback.
    """
    text = normalize_text(description)
    if OWN_CARD_TRANSFER.match(text):
        return "Transfer"
    if direction == TransactionDirection.CREDIT and INCOMING_FROM_PERSON.match(text):
        return "Income"

    combined = normalize_text(f"{description} {merchant or ''}")
    padded = f" {combined}"
    for category, keywords in KEYWORD_RULES:
        if any(f" {keyword}" in padded for keyword in keywords):
            return category

    if mcc in MCC_CATEGORIES:
        return MCC_CATEGORIES[mcc]
    if direction == TransactionDirection.CREDIT:
        return "Income"
    return None
