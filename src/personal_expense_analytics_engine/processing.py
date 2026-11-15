"""Transaction processing on stored data: categorization and recurring payments."""

import sqlite3
from collections import Counter

from personal_expense_analytics_engine.categorization import categorize
from personal_expense_analytics_engine.models import TransactionDirection
from personal_expense_analytics_engine.recurring import RecurringPayment, detect_recurring
from personal_expense_analytics_engine.storage import (
    load_transactions,
    rows_for_categorization,
    set_category,
)


def categorize_stored(conn: sqlite3.Connection, *, overwrite: bool = False) -> dict[str, int]:
    """Categorize stored transactions and return a count per category.

    By default only rows without a category are touched, so manual categories survive.
    overwrite=True recomputes everything (and replaces manual categories).
    """
    counts: Counter[str] = Counter()
    with conn:
        for tid, description, merchant, mcc, direction in rows_for_categorization(
            conn, only_uncategorized=not overwrite
        ):
            category = categorize(
                description,
                direction=TransactionDirection(direction),
                mcc=mcc,
                merchant=merchant,
            )
            counts[category or "Uncategorized"] += 1
            set_category(conn, tid, category)
    return dict(counts)


def find_recurring_stored(conn: sqlite3.Connection, account_id: str | None = None) -> list[RecurringPayment]:
    """Detect recurring payments in stored data. Run categorize_stored first."""
    return detect_recurring(load_transactions(conn, account_id))
