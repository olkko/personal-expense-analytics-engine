"""Spending statistics, monthly trends and unusual-spending detection.

All money is handled as integer minor units. Amounts in different currencies are
never added together: every result is grouped by currency.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median_low

from babel.numbers import get_currency_precision

from personal_expense_analytics_engine._models import Transaction
from personal_expense_analytics_engine._normalization import merchant_key
from personal_expense_analytics_engine._storage import load_transactions

TRANSFER_CATEGORY = "Transfer"
INCOME_CATEGORY = "Income"
NOT_SPENDING = (TRANSFER_CATEGORY, INCOME_CATEGORY)

MONTHS_PER_YEAR = 12
MAD_SCALE = 1.4826  # makes MAD comparable to a standard deviation
SPREAD_FLOOR_DIVISOR = 4  # spread is at least median / 4, so tiny MADs don't over-flag
MIN_SAMPLES = 5
Z_THRESHOLD = 3.5


@dataclass(frozen=True, slots=True)
class MonthlyTotal:
    """Spending and income for one month and currency, in minor units."""

    month: str  # "YYYY-MM"
    currency: str
    spent_minor: int
    income_minor: int

    @property
    def net_minor(self) -> int:
        """Return income minus spending.

        Returns:
            int: Net amount in minor currency units.
        """
        return self.income_minor - self.spent_minor


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    """Spending in one category for one month and currency, in minor units."""

    month: str
    currency: str
    category: str
    spent_minor: int
    count: int


@dataclass(frozen=True, slots=True)
class MonthChange:
    """Change in spending versus the previous calendar month (same currency)."""

    month: str
    currency: str
    spent_minor: int
    previous_minor: int | None
    change_minor: int | None
    change_pct: float | None


@dataclass(frozen=True, slots=True)
class UnusualTransaction:
    """An expense far above the typical amount of its category."""

    transaction_id: str
    date: date
    description: str
    category: str
    currency: str
    amount_minor: int
    typical_minor: int
    score: float
    basis: str


def format_minor(amount_minor: int, currency: str) -> str:
    """Format minor units for display using the currency's decimal precision.

    Args:
        amount_minor (int): Amount in minor currency units.
        currency (str): Three-letter currency code.

    Returns:
        str: Human-readable amount, for example ``"233.41 UAH"``.
    """
    exponent = get_currency_precision(currency)
    value = Decimal(amount_minor) / Decimal(10**exponent)
    return f"{value:.{exponent}f} {currency}"


def monthly_totals(
    conn: sqlite3.Connection, account_id: str | None = None
) -> list[MonthlyTotal]:
    """Calculate spending and income per month and currency.

    Spending is debits except transfers and income. Income is credits categorized
    as income.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str | None): Optional account filter.

    Returns:
        list[MonthlyTotal]: Totals ordered by month, then currency.

    Raises:
        sqlite3.Error: If the database query fails.
    """
    rows = conn.execute(
        """
        SELECT substr(date, 1, 7) AS month,
               currency,
               SUM(CASE WHEN direction = 'debit'
                         AND COALESCE(category, '') NOT IN (:transfer, :income)
                        THEN amount_minor ELSE 0 END),
               SUM(CASE WHEN direction = 'credit' AND category = :income
                        THEN amount_minor ELSE 0 END)
        FROM transactions
        WHERE (:account_id IS NULL OR account_id = :account_id)
        GROUP BY month, currency
        ORDER BY month, currency
        """,
        {
            "transfer": TRANSFER_CATEGORY,
            "income": INCOME_CATEGORY,
            "account_id": account_id,
        },
    ).fetchall()
    return [MonthlyTotal(row[0], row[1], row[2], row[3]) for row in rows]


def category_totals(
    conn: sqlite3.Connection,
    *,
    month: str | None = None,
    account_id: str | None = None,
) -> list[CategoryTotal]:
    """Calculate spending per category, month and currency.

    Uncategorized expenses are reported under ``"Uncategorized"``.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        month (str | None): Optional ``"YYYY-MM"`` filter.
        account_id (str | None): Optional account filter.

    Returns:
        list[CategoryTotal]: Totals ordered by month, currency, then largest first.

    Raises:
        sqlite3.Error: If the database query fails.
    """
    rows = conn.execute(
        """
        SELECT substr(date, 1, 7) AS month,
               currency,
               COALESCE(category, 'Uncategorized') AS cat,
               SUM(amount_minor) AS spent,
               COUNT(*)
        FROM transactions
        WHERE direction = 'debit'
          AND COALESCE(category, '') NOT IN (:transfer, :income)
          AND (:account_id IS NULL OR account_id = :account_id)
          AND (:month IS NULL OR substr(date, 1, 7) = :month)
        GROUP BY month, currency, cat
        ORDER BY month, currency, spent DESC
        """,
        {
            "transfer": TRANSFER_CATEGORY,
            "income": INCOME_CATEGORY,
            "account_id": account_id,
            "month": month,
        },
    ).fetchall()
    return [CategoryTotal(row[0], row[1], row[2], row[3], row[4]) for row in rows]


def partial_months(conn: sqlite3.Connection, today: date) -> set[str]:
    """Return months whose data is probably incomplete.

    The first stored month is partial if data starts after the 1st (the sync window
    cut it off). The current month is partial because it is not over yet.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        today (date): The current date.

    Returns:
        set[str]: Months as ``"YYYY-MM"``.

    Raises:
        sqlite3.Error: If the database query fails.
    """
    first_text, last_text = conn.execute(
        "SELECT MIN(date), MAX(date) FROM transactions"
    ).fetchone()
    if first_text is None:
        return set()
    first = date.fromisoformat(first_text)
    last = date.fromisoformat(last_text)
    partial: set[str] = set()
    if first.day > 1:
        partial.add(first.strftime("%Y-%m"))
    if (today.year, today.month) == (last.year, last.month):
        partial.add(today.strftime("%Y-%m"))
    return partial


def _month_index(month: str) -> int:
    """Convert ``"YYYY-MM"`` to a running month number.

    Args:
        month (str): Month as ``"YYYY-MM"``.

    Returns:
        int: Month count, so consecutive months differ by exactly one.
    """
    year, number = month.split("-")
    return int(year) * MONTHS_PER_YEAR + int(number)


def month_over_month(
    totals: Iterable[MonthlyTotal], *, skip_months: Iterable[str] = ()
) -> list[MonthChange]:
    """Compare each month's spending with the previous calendar month.

    Months in ``skip_months`` (partial months) are neither reported nor used as a
    baseline. The previous value is only used if it is the directly preceding
    calendar month; otherwise the comparison is left empty.

    Args:
        totals (Iterable[MonthlyTotal]): Monthly totals, in any order.
        skip_months (Iterable[str]): Months to ignore, as ``"YYYY-MM"``.

    Returns:
        list[MonthChange]: Changes ordered by month, then currency.
    """
    skip = set(skip_months)
    last_seen: dict[str, tuple[int, int]] = {}  # currency -> (month index, spent)
    changes: list[MonthChange] = []
    for item in sorted(totals, key=lambda t: (t.currency, t.month)):
        if item.month in skip:
            continue
        index = _month_index(item.month)
        last = last_seen.get(item.currency)
        previous_minor = last[1] if last and index - last[0] == 1 else None
        change_minor: int | None = None
        change_pct: float | None = None
        if previous_minor is not None:
            change_minor = item.spent_minor - previous_minor
            if previous_minor:
                change_pct = round(change_minor / previous_minor * 100, 1)
        changes.append(
            MonthChange(
                item.month,
                item.currency,
                item.spent_minor,
                previous_minor,
                change_minor,
                change_pct,
            )
        )
        last_seen[item.currency] = (index, item.spent_minor)
    return sorted(changes, key=lambda c: (c.month, c.currency))


def _flag_outliers(
    items: list[Transaction],
    *,
    category: str,
    basis: str,
    z_threshold: float,
) -> list[UnusualTransaction]:
    """Flag expenses far above the median of one group of similar expenses.

    Args:
        items (list[Transaction]): Expenses of one group, all in the same currency.
        category (str): Category name reported on flagged transactions.
        basis (str): Description of the baseline, for example ``"merchant: koleo"``.
        z_threshold (float): Robust z-score at which a transaction is flagged.

    Returns:
        list[UnusualTransaction]: Flagged transactions.
    """
    amounts = [t.amount_minor for t in items]
    typical = median_low(amounts)
    mad = median_low([abs(a - typical) for a in amounts])
    spread = max(mad, typical // SPREAD_FLOOR_DIVISOR, 1)
    flagged: list[UnusualTransaction] = []
    for t in items:
        score = (t.amount_minor - typical) / (MAD_SCALE * spread)
        if score >= z_threshold:
            flagged.append(
                UnusualTransaction(
                    transaction_id=t.transaction_id,
                    date=t.date,
                    description=t.description,
                    category=category,
                    currency=t.currency,
                    amount_minor=t.amount_minor,
                    typical_minor=typical,
                    score=round(score, 1),
                    basis=basis,
                )
            )
    return flagged


def detect_unusual(
    transactions: Iterable[Transaction],
    *,
    min_samples: int = MIN_SAMPLES,
    z_threshold: float = Z_THRESHOLD,
) -> list[UnusualTransaction]:
    """Find expenses far above their usual amount.

    A merchant with at least ``min_samples`` expenses (same category and currency) is
    compared with its own history. Merchants with fewer expenses are pooled by
    category and compared with that pool if it has ``min_samples`` expenses. The
    typical amount is the median and the spread is the median absolute deviation
    (never below median / 4). A transaction is flagged when its robust z-score is at
    least ``z_threshold``. Transfers, income and uncategorized rows are ignored.

    Args:
        transactions (Iterable[Transaction]): Transactions to analyze.
        min_samples (int): Minimum expenses a group needs before it is analyzed.
        z_threshold (float): Robust z-score at which a transaction is flagged.

    Returns:
        list[UnusualTransaction]: Flagged transactions, highest score first.
    """
    by_merchant: dict[tuple[str, str, str], list[Transaction]] = defaultdict(list)
    for t in transactions:
        if not t.is_expense or t.category is None or t.category in NOT_SPENDING:
            continue
        key = merchant_key(t.merchant or t.description)
        by_merchant[(t.category, t.currency, key)].append(t)

    found: list[UnusualTransaction] = []
    leftovers: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for (category, currency, key), items in by_merchant.items():
        if len(items) >= min_samples:
            found.extend(
                _flag_outliers(
                    items,
                    category=category,
                    basis=f"merchant: {key}",
                    z_threshold=z_threshold,
                )
            )
        else:
            leftovers[(category, currency)].extend(items)

    for (category, _currency), items in leftovers.items():
        if len(items) >= min_samples:
            found.extend(
                _flag_outliers(
                    items,
                    category=category,
                    basis=f"category: {category}",
                    z_threshold=z_threshold,
                )
            )
    return sorted(found, key=lambda u: u.score, reverse=True)


def detect_unusual_stored(
    conn: sqlite3.Connection, account_id: str | None = None
) -> list[UnusualTransaction]:
    """Run unusual-spending detection on stored transactions.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str | None): Optional account filter.

    Returns:
        list[UnusualTransaction]: Flagged transactions, highest score first.

    Raises:
        sqlite3.Error: If the database query fails.
    """
    return detect_unusual(load_transactions(conn, account_id))
