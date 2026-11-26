"""Shape stored transactions and analytics results into tables for the dashboard.

Everything here is plain pandas so it can be tested without Streamlit. Amounts are
converted from integer minor units to major units (floats) only at this display
boundary; all calculations upstream stay in minor units. One currency is shown at
a time because amounts in different currencies are never added together.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

import pandas as pd
from babel.numbers import get_currency_precision

from personal_expense_analytics_engine._analyst import _scoped_partial_months
from personal_expense_analytics_engine._analytics import (
    NOT_SPENDING,
    category_totals,
    detect_unusual,
    monthly_totals,
)
from personal_expense_analytics_engine._recurring import detect_recurring
from personal_expense_analytics_engine._storage import load_transactions, set_category

OTHER_CATEGORY = "Other"
MAX_CATEGORY_SERIES = 7  # the 8th colour slot is reserved for "Other"


@dataclass(frozen=True)
class DashboardData:
    """Everything the page shows for one database, account and currency."""

    monthly: pd.DataFrame
    categories: pd.DataFrame
    transactions: pd.DataFrame
    recurring: pd.DataFrame
    unusual: pd.DataFrame

# Approximate occurrences per month, used to compare recurring costs.
_PER_MONTH = {"weekly": 52 / 12, "biweekly": 26 / 12, "monthly": 1.0, "yearly": 1 / 12}


def to_major(amount_minor: int, currency: str) -> float:
    """Convert minor units to a major-unit float for charts and tables.

    Args:
        amount_minor (int): Amount in minor currency units.
        currency (str): Three-letter currency code.

    Returns:
        float: Amount in major units, for example ``42.5`` for 4250 grosze.
    """
    return amount_minor / 10 ** get_currency_precision(currency)


def currencies(conn: sqlite3.Connection, account_id: str | None = None) -> list[str]:
    """List stored currencies, most transactions first.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str | None): Optional account filter.

    Returns:
        list[str]: Currency codes.
    """
    rows = conn.execute(
        """
        SELECT currency FROM transactions
        WHERE (:account_id IS NULL OR account_id = :account_id)
        GROUP BY currency ORDER BY COUNT(*) DESC, currency
        """,
        {"account_id": account_id},
    ).fetchall()
    return [row[0] for row in rows]


def accounts(conn: sqlite3.Connection) -> list[str]:
    """List stored account identifiers.

    Args:
        conn (sqlite3.Connection): Database connection to query.

    Returns:
        list[str]: Account identifiers in alphabetical order.
    """
    rows = conn.execute(
        "SELECT DISTINCT account_id FROM transactions ORDER BY account_id"
    ).fetchall()
    return [row[0] for row in rows]


def account_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """Return privacy-safe labels for accounts that have stored metadata."""
    rows = conn.execute(
        "SELECT account_id, display_name, currency, last4 FROM account_metadata"
    ).fetchall()
    labels: dict[str, str] = {}
    for account_id, name, currency, last4 in rows:
        parts = [str(name).strip() if name else "Account"]
        if currency:
            parts.append(str(currency))
        if last4:
            parts.append(f"••••{last4}")
        labels[account_id] = " · ".join(parts)
    return labels


def monthly_frame(
    conn: sqlite3.Connection,
    currency: str,
    *,
    account_id: str | None = None,
    today: date | None = None,
) -> pd.DataFrame:
    """Monthly spending, income and net flow for one currency.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        currency (str): Currency to report.
        account_id (str | None): Optional account filter.
        today (date | None): Current date, used to flag incomplete months.

    Returns:
        pd.DataFrame: Columns ``month``, ``spent``, ``income``, ``net`` and
            ``partial``, ordered by month.
    """
    partial = _scoped_partial_months(conn, account_id, today or date.today())
    rows = [
        {
            "month": item.month,
            "spent": to_major(item.spent_minor, currency),
            "income": to_major(item.income_minor, currency),
            "net": to_major(item.income_minor - item.spent_minor, currency),
            "partial": item.month in partial,
        }
        for item in monthly_totals(conn, account_id)
        if item.currency == currency
    ]
    return pd.DataFrame(rows, columns=["month", "spent", "income", "net", "partial"])


def category_frame(
    conn: sqlite3.Connection, currency: str, *, account_id: str | None = None
) -> pd.DataFrame:
    """Spending per month and category for one currency.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        currency (str): Currency to report.
        account_id (str | None): Optional account filter.

    Returns:
        pd.DataFrame: Columns ``month``, ``category``, ``spent`` and ``count``.
    """
    rows = [
        {
            "month": item.month,
            "category": item.category,
            "spent": to_major(item.spent_minor, currency),
            "count": item.count,
        }
        for item in category_totals(conn, account_id=account_id)
        if item.currency == currency
    ]
    return pd.DataFrame(rows, columns=["month", "category", "spent", "count"])


def category_order(categories: pd.DataFrame) -> list[str]:
    """Rank categories by total spending over the whole period.

    The order depends only on the full history, not on the selected month, so a
    category keeps the same colour when filters change.

    Args:
        categories (pd.DataFrame): Output of :func:`category_frame`.

    Returns:
        list[str]: Category names, largest total first.
    """
    if categories.empty:
        return []
    totals: dict[str, float] = {}
    for name, spent in zip(categories["category"], categories["spent"]):
        totals[name] = totals.get(name, 0.0) + float(spent)
    return sorted(totals, key=lambda name: (-totals[name], name))


def fold_categories(
    categories: pd.DataFrame, order: list[str], keep: int = MAX_CATEGORY_SERIES
) -> pd.DataFrame:
    """Fold all but the ``keep`` largest categories into ``"Other"``.

    Args:
        categories (pd.DataFrame): Output of :func:`category_frame`.
        order (list[str]): Ranking from :func:`category_order`.
        keep (int): Number of categories that keep their own series.

    Returns:
        pd.DataFrame: Same columns, with small categories summed per month.
    """
    if len(order) <= keep + 1:  # folding one category into "Other" hides it for nothing
        return categories.copy()
    kept = order[:keep]
    folded = categories.assign(
        category=categories["category"].where(
            categories["category"].isin(kept), OTHER_CATEGORY
        )
    )
    return folded.groupby(["month", "category"], as_index=False).agg(
        spent=("spent", "sum"), count=("count", "sum")
    )


def category_changes(
    categories: pd.DataFrame, month: str, previous_month: str
) -> pd.DataFrame:
    """Compare category spending in ``month`` with ``previous_month``.

    Args:
        categories (pd.DataFrame): Output of :func:`category_frame`.
        month (str): Month to report, ``YYYY-MM``.
        previous_month (str): Month to compare against, ``YYYY-MM``.

    Returns:
        pd.DataFrame: Columns ``category``, ``spent``, ``previous``, ``change`` and
            ``change_pct`` (``NaN`` when there was no spending before), largest
            current spending first. Categories absent in one month count as zero.
    """
    pivot = (
        categories[categories["month"].isin([month, previous_month])]
        .pivot_table(index="category", columns="month", values="spent", aggfunc="sum")
        .reindex(columns=[month, previous_month])
        .fillna(0.0)
    )
    result = pd.DataFrame(
        {
            "category": pivot.index,
            "spent": pivot[month].to_numpy(),
            "previous": pivot[previous_month].to_numpy(),
        }
    )
    result["change"] = result["spent"] - result["previous"]
    result["change_pct"] = (
        result["change"] / result["previous"].where(result["previous"] != 0) * 100
    )
    return result.sort_values(["spent", "previous"], ascending=False, ignore_index=True)


def transactions_frame(
    conn: sqlite3.Connection, currency: str, *, account_id: str | None = None
) -> pd.DataFrame:
    """All transactions in one currency, newest first.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        currency (str): Currency to report.
        account_id (str | None): Optional account filter.

    Returns:
        pd.DataFrame: Columns ``transaction_id``, ``date``, ``description``,
            ``merchant``, ``category``, ``direction``, ``amount`` (signed: debits
            negative) and ``counts_as_spending``.
    """
    rows = [
        {
            "transaction_id": txn.transaction_id,
            "date": txn.date,
            "description": txn.description,
            "merchant": txn.merchant or "",
            "category": txn.category or "Uncategorized",
            "direction": str(txn.direction),
            "amount": to_major(txn.signed_amount_minor, currency),
            "counts_as_spending": txn.is_expense
            and (txn.category or "") not in NOT_SPENDING,
        }
        for txn in load_transactions(conn, account_id)
        if txn.currency == currency
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "transaction_id",
            "date",
            "description",
            "merchant",
            "category",
            "direction",
            "amount",
            "counts_as_spending",
        ],
    )


def save_category_edits(
    conn: sqlite3.Connection,
    original: pd.DataFrame,
    edited: pd.DataFrame,
) -> int:
    """Persist category edits from the dashboard transaction editor.

    The transaction ID is used to match rows if displayed rows are reordered.
    The visible "Uncategorized" option is stored as a NULL category.

    Args:
        conn: Open database connection to update.
        original: Unedited transaction rows shown in the editor.
        edited: Rows returned by the editor after user changes.

    Returns:
        Number of transactions whose categories changed.

    Raises:
        ValueError: If the editor changed its rows or supplied an invalid category.
        sqlite3.Error: If a category update cannot be saved.
    """
    original_categories = original.set_index("transaction_id")["category"]
    edited_categories = edited.set_index("transaction_id")["category"]
    if (
        not original_categories.index.is_unique
        or not edited_categories.index.is_unique
        or set(original_categories.index) != set(edited_categories.index)
    ):
        raise ValueError("Edited transactions do not match the displayed rows.")

    changes: list[tuple[str, str | None]] = []
    for transaction_id in original_categories.index:
        if not isinstance(transaction_id, str):
            raise ValueError("Transaction IDs must be strings.")
        current_category = original_categories.loc[transaction_id]
        new_category = edited_categories[transaction_id]
        if not isinstance(new_category, str):
            raise ValueError("Transaction category must be a string.")
        if current_category != new_category:
            changes.append(
                (
                    transaction_id,
                    None if new_category == "Uncategorized" else new_category,
                )
            )

    for transaction_id, category in changes:
        set_category(conn, transaction_id, category)
    if changes:
        conn.commit()
    return len(changes)


def recurring_frame(
    conn: sqlite3.Connection, currency: str, *, account_id: str | None = None
) -> pd.DataFrame:
    """Detected recurring payments in one currency, largest monthly cost first.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        currency (str): Currency to report.
        account_id (str | None): Optional account filter.

    Returns:
        pd.DataFrame: Columns ``merchant``, ``frequency``, ``typical_amount``,
            ``per_month``, ``occurrences``, ``last_date`` and ``next_expected``.
    """
    rows = [
        {
            "merchant": item.key,
            "frequency": item.frequency,
            "typical_amount": to_major(item.typical_amount_minor, currency),
            "per_month": to_major(item.typical_amount_minor, currency)
            * _PER_MONTH.get(item.frequency, 0.0),
            "occurrences": item.occurrences,
            "last_date": item.last_date,
            "next_expected": item.next_expected_date,
        }
        for item in detect_recurring(load_transactions(conn, account_id))
        if item.currency == currency
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "merchant",
            "frequency",
            "typical_amount",
            "per_month",
            "occurrences",
            "last_date",
            "next_expected",
        ],
    )
    return frame.sort_values("per_month", ascending=False, ignore_index=True)


def unusual_frame(
    conn: sqlite3.Connection, currency: str, *, account_id: str | None = None
) -> pd.DataFrame:
    """Unusually large purchases in one currency, highest score first.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        currency (str): Currency to report.
        account_id (str | None): Optional account filter.

    Returns:
        pd.DataFrame: Columns ``date``, ``description``, ``category``, ``amount``,
            ``typical``, ``times_typical``, ``score`` and ``compared_with``.
    """
    rows = [
        {
            "date": item.date,
            "description": item.description,
            "category": item.category,
            "amount": to_major(item.amount_minor, currency),
            "typical": to_major(item.typical_minor, currency),
            "times_typical": (
                item.amount_minor / item.typical_minor if item.typical_minor else None
            ),
            "score": item.score,
            "compared_with": item.basis,
        }
        for item in detect_unusual(load_transactions(conn, account_id))
        if item.currency == currency
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "description",
            "category",
            "amount",
            "typical",
            "times_typical",
            "score",
            "compared_with",
        ],
    )
