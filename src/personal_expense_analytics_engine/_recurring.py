"""Detect recurring payments (subscriptions, bills) from expense history."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from personal_expense_analytics_engine._models import Transaction
from personal_expense_analytics_engine._normalization import merchant_key

# (name, typical gap in days, allowed deviation in days)
PERIODS = [
    ("weekly", 7, 1),
    ("biweekly", 14, 2),
    ("monthly", 30, 3),
    ("yearly", 365, 10),
]


@dataclass(frozen=True, slots=True)
class RecurringPayment:
    key: str
    currency: str
    frequency: str
    typical_amount_minor: int
    occurrences: int
    last_date: date
    next_expected_date: date


def _middle(values: list[int]) -> int:
    """Return the middle value after sorting the input.

    Args:
        values (list[int]): Integer values to order.

    Returns:
        int: Middle value, or the upper middle value for an even-sized list.
    """
    return sorted(values)[len(values) // 2]


def _classify(gaps: list[int]) -> tuple[str, int] | None:
    """Match transaction-day gaps to a supported recurring frequency.

    Args:
        gaps (list[int]): Number of days between consecutive transactions.

    Returns:
        tuple[str, int] | None: Frequency name and typical period in days, or None
            if the gaps do not match a supported period.
    """
    typical_gap = _middle(gaps)
    for name, days, tolerance in PERIODS:
        if abs(typical_gap - days) <= tolerance and all(
            abs(gap - typical_gap) <= tolerance for gap in gaps
        ):
            return name, days
    return None


def detect_recurring(
    transactions: list[Transaction],
    *,
    min_occurrences: int = 3,
    amount_tolerance_pct: int = 10,
) -> list[RecurringPayment]:
    """Find expenses repeated at regular intervals with similar amounts.

    Transfers are ignored. All arithmetic is done on integer minor units.

    Args:
        transactions (list[Transaction]): Transactions to analyze.
        min_occurrences (int): Minimum number of similar transactions required.
        amount_tolerance_pct (int): Maximum percentage deviation from the typical amount.

    Returns:
        list[RecurringPayment]: Detected recurring payments, ordered by next expected date.
    """
    groups: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in transactions:
        if not t.is_expense or t.category == "Transfer":
            continue
        key = merchant_key(t.merchant or t.description)
        if key:
            groups[(key, t.currency)].append(t)

    found: list[RecurringPayment] = []
    for (key, currency), items in groups.items():
        typical = _middle([t.amount_minor for t in items])
        similar = sorted(
            (
                t
                for t in items
                if abs(t.amount_minor - typical) * 100 <= typical * amount_tolerance_pct
            ),
            key=lambda t: t.date,
        )
        if len(similar) < min_occurrences:
            continue
        gaps = [(b.date - a.date).days for a, b in zip(similar, similar[1:])]
        period = _classify(gaps)
        if period is None:
            continue
        name, days = period
        last = similar[-1].date
        found.append(
            RecurringPayment(
                key=key,
                currency=currency,
                frequency=name,
                typical_amount_minor=_middle([t.amount_minor for t in similar]),
                occurrences=len(similar),
                last_date=last,
                next_expected_date=last + timedelta(days=days),
            )
        )
    return sorted(found, key=lambda r: (r.next_expected_date, r.key))
