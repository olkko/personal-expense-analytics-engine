"""Generate explainable monthly spending insights from local analytics."""

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from personal_expense_analytics_engine._analytics import (
    CategoryTotal,
    UnusualTransaction,
    category_totals,
    detect_unusual,
    format_minor,
    monthly_totals,
)
from personal_expense_analytics_engine._models import Transaction
from personal_expense_analytics_engine._normalization import merchant_key
from personal_expense_analytics_engine._ollama import generate_narrative
from personal_expense_analytics_engine._recurring import detect_recurring
from personal_expense_analytics_engine._storage import load_transactions

STABLE_RECURRING_CHANGE_PCT = 10.0


@dataclass(frozen=True, slots=True)
class CategoryInsight:
    """Monthly category totals and their change from the previous month."""

    category: str
    spent_minor: int
    previous_minor: int | None
    change_minor: int | None
    change_pct: float | None
    unusual_count: int


@dataclass(frozen=True, slots=True)
class MonthlySpendingInsight:
    """Structured monthly spending comparison and its readable narrative."""

    month: str
    previous_month: str
    currency: str
    spent_minor: int
    previous_spent_minor: int | None
    change_minor: int | None
    change_pct: float | None
    categories: tuple[CategoryInsight, ...]
    unusual_transactions: tuple[UnusualTransaction, ...]
    recurring_spent_minor: int | None
    previous_recurring_spent_minor: int | None
    facts_text: str
    text: str
    model: str


@dataclass(frozen=True, slots=True)
class QuestionInsight:
    """Answer to a dashboard question, with privacy-safe model evidence."""

    text: str
    facts_text: str
    model: str | None


def _previous_month(month: str) -> str:
    """Validate a YYYY-MM month and return its preceding calendar month.

    Args:
        month (str): Month to compare, in ``YYYY-MM`` format.

    Returns:
        str: Previous month in ``YYYY-MM`` format.

    Raises:
        ValueError: If the input is not a valid ``YYYY-MM`` month.
    """
    try:
        parsed = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM format.") from exc
    if parsed.strftime("%Y-%m") != month:
        raise ValueError("month must use YYYY-MM format.")
    if parsed.month == 1:
        return f"{parsed.year - 1:04d}-12"
    return f"{parsed.year:04d}-{parsed.month - 1:02d}"


def _scoped_partial_months(
    conn: sqlite3.Connection,
    account_id: str | None,
    today: date,
) -> set[str]:
    """Find possibly incomplete first and current months for an account scope.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str | None): Optional account identifier.
        today (date): Date used to identify the current month.

    Returns:
        set[str]: Month labels whose data may be incomplete.
    """
    row = conn.execute(
        """
        SELECT MIN(date), MAX(date)
        FROM transactions
        WHERE (:account_id IS NULL OR account_id = :account_id)
        """,
        {"account_id": account_id},
    ).fetchone()
    first_text, last_text = row
    if first_text is None or last_text is None:
        return set()

    first = date.fromisoformat(first_text)
    last = date.fromisoformat(last_text)
    partial: set[str] = set()
    if first.day > 1:
        partial.add(first.strftime("%Y-%m"))
    if (last.year, last.month) == (today.year, today.month):
        partial.add(today.strftime("%Y-%m"))
    return partial


def _category_insights(
    current: list[CategoryTotal],
    previous: list[CategoryTotal],
    unusual: tuple[UnusualTransaction, ...],
    *,
    has_previous: bool,
    limit: int,
) -> tuple[CategoryInsight, ...]:
    """Compare category spending and attach counts of flagged purchases.

    Args:
        current (list[CategoryTotal]): Category totals for the requested month.
        previous (list[CategoryTotal]): Category totals for the preceding month.
        unusual (tuple[UnusualTransaction, ...]): Unusual purchases in the requested
            month and currency.
        has_previous (bool): Whether the preceding month has comparable spending data.
        limit (int): Maximum number of categories to include.

    Returns:
        tuple[CategoryInsight, ...]: Largest increases, or largest current categories
            when no previous-month baseline exists.
    """
    current_by_name = {item.category: item.spent_minor for item in current}
    previous_by_name = {item.category: item.spent_minor for item in previous}
    unusual_by_category: dict[str, int] = defaultdict(int)
    for item in unusual:
        unusual_by_category[item.category] += 1

    insights: list[CategoryInsight] = []
    for category in current_by_name.keys() | previous_by_name.keys():
        spent = current_by_name.get(category, 0)
        previous_spent = previous_by_name.get(category, 0)
        change = spent - previous_spent if has_previous else None
        change_pct = (
            round(change / previous_spent * 100, 1)
            if change is not None and previous_spent > 0
            else None
        )
        insights.append(
            CategoryInsight(
                category=category,
                spent_minor=spent,
                previous_minor=previous_spent if has_previous else None,
                change_minor=change,
                change_pct=change_pct,
                unusual_count=unusual_by_category.get(category, 0),
            )
        )

    if has_previous:
        insights.sort(
            key=lambda item: (
                item.change_minor is not None and item.change_minor > 0,
                item.change_minor or 0,
                item.spent_minor,
            ),
            reverse=True,
        )
    else:
        insights.sort(key=lambda item: item.spent_minor, reverse=True)
    return tuple(insights[:limit])


def _recurring_month_total(
    transactions: list[Transaction],
    recurring_keys: set[tuple[str, str]],
    month: str,
) -> int:
    """Sum expenses from merchants identified as recurring in a month.

    Args:
        transactions (list[Transaction]): Transactions for one account scope.
        recurring_keys (set[tuple[str, str]]): Detected merchant and currency pairs.
        month (str): Month to sum in ``YYYY-MM`` format.

    Returns:
        int: Recurring expense total in minor units.
    """
    return sum(
        transaction.amount_minor
        for transaction in transactions
        if transaction.is_expense
        and transaction.date.strftime("%Y-%m") == month
        and (
            merchant_key(transaction.merchant or transaction.description),
            transaction.currency,
        )
        in recurring_keys
    )


def _percent_text(value: float) -> str:
    """Format a percentage without unnecessary trailing zeroes.

    Args:
        value (float): Percentage value.

    Returns:
        str: Percentage suitable for a short report.
    """
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _build_narrative(
    *,
    month: str,
    previous_month: str,
    currency: str,
    spent_minor: int,
    previous_spent_minor: int | None,
    change_minor: int | None,
    change_pct: float | None,
    categories: tuple[CategoryInsight, ...],
    recurring_spent_minor: int | None,
    previous_recurring_spent_minor: int | None,
    partial_months: set[str],
) -> str:
    """Build a plain-language report from calculated evidence.

    Args:
        month (str): Month being analyzed.
        previous_month (str): Previous calendar month.
        currency (str): Currency for all displayed amounts.
        spent_minor (int): Current month's spending.
        previous_spent_minor (int | None): Previous month's spending, if available.
        change_minor (int | None): Absolute change from the previous month.
        change_pct (float | None): Percentage change, if calculable.
        categories (tuple[CategoryInsight, ...]): Category comparison evidence.
        recurring_spent_minor (int | None): Current recurring expense total.
        previous_recurring_spent_minor (int | None): Previous recurring expense total.
        partial_months (set[str]): Months whose data may be incomplete.

    Returns:
        str: Evidence-based monthly spending narrative.
    """
    current_amount = format_minor(spent_minor, currency)
    if previous_spent_minor is None:
        lines = [
            f"Recorded spending in {month} was {current_amount}.",
            f"There is no comparable spending data for {previous_month}, "
            "so a month-over-month change cannot be calculated.",
        ]
        if categories:
            lines.append("Largest spending categories:")
            lines.extend(
                f"- {item.category}: {format_minor(item.spent_minor, currency)}."
                for item in categories
                if item.spent_minor > 0
            )
    else:
        previous_amount = format_minor(previous_spent_minor, currency)
        if change_pct is None:
            comparison = (
                f"Spending changed by {format_minor(abs(change_minor or 0), currency)} "
                f"compared with {previous_month} "
                f"({current_amount} versus {previous_amount}); "
                "a percentage change is unavailable because previous spending was zero."
            )
        else:
            direction = (
                "increased"
                if change_minor and change_minor > 0
                else (
                    "decreased"
                    if change_minor and change_minor < 0
                    else "was unchanged"
                )
            )
            comparison = (
                f"Spending {direction} by {_percent_text(abs(change_pct))}% "
                f"compared with {previous_month} "
                f"({current_amount} versus {previous_amount})."
            )
        lines = [comparison]
        increases = [
            item
            for item in categories
            if item.change_minor is not None and item.change_minor > 0
        ]
        if increases:
            lines.append("Largest category increases:")
            for item in increases:
                change_text = format_minor(item.change_minor or 0, currency)
                detail = f"- {item.category}: +{change_text}"
                if item.change_pct is not None:
                    detail += f" ({_percent_text(item.change_pct)}% increase)"
                if item.unusual_count:
                    count_label = (
                        "unusually large purchase"
                        if item.unusual_count == 1
                        else "unusually large purchases"
                    )
                    detail += f", including {item.unusual_count} {count_label}"
                lines.append(detail + ".")
        else:
            lines.append("No category increased compared with the previous month.")

    if recurring_spent_minor and previous_recurring_spent_minor:
        recurring_change = recurring_spent_minor - previous_recurring_spent_minor
        recurring_pct = abs(recurring_change) / previous_recurring_spent_minor * 100
        if recurring_pct <= STABLE_RECURRING_CHANGE_PCT:
            lines.append(
                "Identified recurring expenses remained broadly stable "
                f"({format_minor(recurring_spent_minor, currency)} versus "
                f"{format_minor(previous_recurring_spent_minor, currency)})."
            )
        else:
            direction = "increased" if recurring_change > 0 else "decreased"
            lines.append(
                f"Identified recurring expenses {direction} by "
                f"{format_minor(abs(recurring_change), currency)} "
                f"({_percent_text(recurring_pct)}%)."
            )

    incomplete = sorted({month, previous_month} & partial_months)
    if incomplete:
        lines.append(
            "Note: data for "
            + " and ".join(incomplete)
            + " may be incomplete, so treat this comparison cautiously."
        )

    if previous_spent_minor is not None and change_minor and change_minor > 0:
        biggest_increase = next(
            (
                item
                for item in categories
                if item.change_minor is not None and item.change_minor > 0
            ),
            None,
        )
        if biggest_increase:
            lines.append(
                "Suggestion: review the transactions in "
                f"{biggest_increase.category}, the largest category increase. "
                "This report describes recorded activity and does not predict "
                "future savings."
            )
    return "\n".join(lines)


def _build_redacted_narrative(
    *,
    previous_spent_minor: int | None,
    change_minor: int | None,
    categories: tuple[CategoryInsight, ...],
    recurring_spent_minor: int | None,
    partial_months: set[str],
) -> str:
    """Build model evidence without amounts, counts, dates, or identifiers."""
    lines: list[str] = []
    if previous_spent_minor is None:
        lines.append("There is no comparable previous-period spending data.")
    elif change_minor is not None and change_minor > 0:
        lines.append("Spending increased compared with the previous period.")
    elif change_minor is not None and change_minor < 0:
        lines.append("Spending decreased compared with the previous period.")
    else:
        lines.append("Spending was unchanged compared with the previous period.")

    if categories:
        lines.append(
            "The main spending categories were: "
            + ", ".join(item.category for item in categories)
            + "."
        )
    if recurring_spent_minor is not None:
        lines.append("Recurring expenses were identified.")
    if partial_months:
        lines.append("Some period data may be incomplete.")
    return " ".join(lines)


def monthly_spending_insight(
    conn: sqlite3.Connection,
    month: str,
    currency: str,
    *,
    account_id: str | None = None,
    today: date | None = None,
    top_categories: int = 3,
    model: str = "qwen3:0.6b",
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_timeout: float = 120,
) -> MonthlySpendingInsight:
    """Create an Ollama-written narrative grounded in local spending analytics.

    Metrics and unusual-spending flags are calculated locally. Only a text summary
    of those computed facts is sent to the local Ollama service; raw transactions
    are not sent.

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        month (str): Month to analyze, in ``YYYY-MM`` format.
        currency (str): Three-letter currency code; currencies are never combined.
        account_id (str | None): Optional account identifier to filter by.
        today (date | None): Optional current date for incomplete-month warnings.
        top_categories (int): Maximum number of categories to report.
        model (str): Installed local Ollama model name.
        ollama_url (str): Local Ollama HTTP API origin.
        ollama_timeout (float): Maximum time to wait for Ollama.

    Returns:
        MonthlySpendingInsight: Structured metrics, verified facts, and AI narrative.

    Raises:
        ValueError: If inputs are invalid or the requested month has no spending data
            in that currency.
        sqlite3.Error: If a database query fails.
        OllamaAnalystError: If the local Ollama service fails or returns invalid data.
    """
    previous_month = _previous_month(month)
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("currency must be a three-letter code.")
    if top_categories < 1:
        raise ValueError("top_categories must be at least one.")

    totals = {
        (item.month, item.currency): item for item in monthly_totals(conn, account_id)
    }
    current_total = totals.get((month, normalized_currency))
    if current_total is None:
        raise ValueError(
            f"No spending data found for {month} in {normalized_currency}."
        )
    previous_total = totals.get((previous_month, normalized_currency))
    previous_spent = previous_total.spent_minor if previous_total else None
    change = (
        current_total.spent_minor - previous_spent
        if previous_spent is not None
        else None
    )
    change_pct = (
        round(change / previous_spent * 100, 1)
        if change is not None and previous_spent
        else None
    )

    transactions = load_transactions(conn, account_id)
    unusual = tuple(
        item
        for item in detect_unusual(transactions)
        if item.date.strftime("%Y-%m") == month and item.currency == normalized_currency
    )
    current_categories = category_totals(
        conn,
        month=month,
        account_id=account_id,
    )
    previous_categories = category_totals(
        conn,
        month=previous_month,
        account_id=account_id,
    )
    categories = _category_insights(
        [item for item in current_categories if item.currency == normalized_currency],
        [item for item in previous_categories if item.currency == normalized_currency],
        unusual,
        has_previous=previous_total is not None,
        limit=top_categories,
    )

    recurring = detect_recurring(transactions)
    recurring_keys = {(item.key, item.currency) for item in recurring}
    recurring_spent = _recurring_month_total(transactions, recurring_keys, month)
    previous_recurring_spent = (
        _recurring_month_total(transactions, recurring_keys, previous_month)
        if previous_total is not None
        else None
    )
    if not recurring_keys or recurring_spent == 0:
        recurring_spent = None
    if previous_recurring_spent == 0:
        previous_recurring_spent = None

    partial = _scoped_partial_months(
        conn,
        account_id,
        today if today is not None else date.today(),
    )
    facts_text = _build_narrative(
        month=month,
        previous_month=previous_month,
        currency=normalized_currency,
        spent_minor=current_total.spent_minor,
        previous_spent_minor=previous_spent,
        change_minor=change,
        change_pct=change_pct,
        categories=categories,
        recurring_spent_minor=recurring_spent,
        previous_recurring_spent_minor=previous_recurring_spent,
        partial_months=partial,
    )
    text = generate_narrative(
        _build_redacted_narrative(
            previous_spent_minor=previous_spent,
            change_minor=change,
            categories=categories,
            recurring_spent_minor=recurring_spent,
            partial_months=partial,
        ),
        model=model,
        base_url=ollama_url,
        timeout=ollama_timeout,
    )
    return MonthlySpendingInsight(
        month=month,
        previous_month=previous_month,
        currency=normalized_currency,
        spent_minor=current_total.spent_minor,
        previous_spent_minor=previous_spent,
        change_minor=change,
        change_pct=change_pct,
        categories=categories,
        unusual_transactions=unusual,
        recurring_spent_minor=recurring_spent,
        previous_recurring_spent_minor=previous_recurring_spent,
        facts_text=facts_text,
        text=text,
        model=model,
    )


def question_insight(
    conn: sqlite3.Connection,
    question: str,
    month: str,
    currency: str,
    *,
    account_id: str | None = None,
    model: str = "qwen3:0.6b",
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_timeout: float = 120,
) -> QuestionInsight:
    """Answer a supported question without sending financial numbers to Ollama.

    Factual questions are answered entirely by local Python. Advice questions send
    only category names and qualitative observations to the language model; amounts,
    counts, transaction descriptions, merchants, and identifiers stay local.
    """
    normalized_question = question.strip().lower()
    normalized_currency = currency.strip().upper()
    if not normalized_question:
        raise ValueError("Enter a question first.")
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("currency must be a three-letter code.")

    transactions = [
        item
        for item in load_transactions(conn, account_id)
        if item.currency == normalized_currency
        and item.date.strftime("%Y-%m") == month
        and item.is_expense
        and (item.category or "") not in ("Transfer", "Income")
    ]
    asks_for_biggest = any(
        word in normalized_question for word in ("biggest", "largest", "most expensive")
    )
    asks_for_purchase = any(
        word in normalized_question for word in ("purchase", "expense", "transaction")
    )
    if asks_for_biggest and asks_for_purchase:
        if not transactions:
            raise ValueError(f"No spending data found for {month} in {normalized_currency}.")
        biggest = max(transactions, key=lambda item: item.amount_minor)
        label = biggest.merchant or biggest.description
        return QuestionInsight(
            text=(
                f"Your biggest purchase in {month} was {label}: "
                f"{format_minor(biggest.amount_minor, normalized_currency)}."
            ),
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    explicit_months = list(dict.fromkeys(re.findall(r"\b20\d{2}-\d{2}\b", normalized_question)))
    if any(word in normalized_question for word in ("compare", "versus", "vs", "difference", "change")):
        compared_months = explicit_months[:2]
        if len(compared_months) < 2:
            compared_months = [_previous_month(month), month]
        totals = {
            item.month: item.spent_minor
            for item in monthly_totals(conn, account_id)
            if item.currency == normalized_currency
        }
        first, second = compared_months
        first_total = totals.get(first, 0)
        second_total = totals.get(second, 0)
        change = second_total - first_total
        direction = "increased" if change > 0 else "decreased" if change < 0 else "did not change"
        return QuestionInsight(
            text=(
                f"Spending {direction} from {format_minor(first_total, normalized_currency)} "
                f"in {first} to {format_minor(second_total, normalized_currency)} in {second}. "
                f"The difference was {format_minor(abs(change), normalized_currency)}."
            ),
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    if any(word in normalized_question for word in ("how much", "total", "spent")):
        total = sum(item.amount_minor for item in transactions)
        return QuestionInsight(
            text=f"You spent {format_minor(total, normalized_currency)} in {month}.",
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    if "category" in normalized_question and any(
        word in normalized_question for word in ("biggest", "largest", "most", "highest")
    ):
        category_rows = [
            item
            for item in category_totals(conn, month=month, account_id=account_id)
            if item.currency == normalized_currency
        ]
        if not category_rows:
            raise ValueError(f"No spending data found for {month} in {normalized_currency}.")
        leader = category_rows[0]
        return QuestionInsight(
            text=(
                f"Your largest spending category in {month} was {leader.category}: "
                f"{format_minor(leader.spent_minor, normalized_currency)}."
            ),
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    if any(word in normalized_question for word in ("recurring", "subscription", "subscriptions")):
        recurring = [
            item
            for item in detect_recurring(load_transactions(conn, account_id))
            if item.currency == normalized_currency
        ]
        if not recurring:
            return QuestionInsight(
                text="No recurring payments were detected in the stored history.",
                facts_text="Calculated locally. No financial data was sent to Ollama.",
                model=None,
            )
        labels = ", ".join(f"{item.key} ({item.frequency})" for item in recurring)
        return QuestionInsight(
            text=f"Detected recurring payments: {labels}.",
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    if any(word in normalized_question for word in ("unusual", "unexpected", "surprising")):
        unusual = [
            item
            for item in detect_unusual(load_transactions(conn, account_id))
            if item.currency == normalized_currency and item.date.strftime("%Y-%m") == month
        ]
        if not unusual:
            return QuestionInsight(
                text=f"No unusual purchases were detected in {month}.",
                facts_text="Calculated locally. No financial data was sent to Ollama.",
                model=None,
            )
        lines = [
            f"{item.description}: {format_minor(item.amount_minor, normalized_currency)}"
            for item in sorted(unusual, key=lambda item: item.amount_minor, reverse=True)
        ]
        return QuestionInsight(
            text="Unusual purchases:\n\n" + "\n".join(f"- {line}" for line in lines),
            facts_text="Calculated locally. No financial data was sent to Ollama.",
            model=None,
        )

    if any(word in normalized_question for word in ("save", "saving", "reduce", "cut")):
        category_rows = [
            item
            for item in category_totals(conn, month=month, account_id=account_id)
            if item.currency == normalized_currency
        ]
        if not category_rows:
            raise ValueError(f"No spending data found for {month} in {normalized_currency}.")
        categories = ", ".join(item.category for item in category_rows[:3])
        recurring = detect_recurring(load_transactions(conn, account_id))
        evidence = (
            f"The largest spending categories for the selected period are: {categories}. "
            + (
                "Recurring payments were detected; review them for subscriptions you no longer use."
                if recurring
                else "No recurring payment pattern was detected."
            )
        )
        return QuestionInsight(
            text=generate_narrative(
                evidence,
                model=model,
                base_url=ollama_url,
                timeout=ollama_timeout,
            ),
            facts_text=evidence,
            model=model,
        )

    raise ValueError(
        "Try asking about your biggest purchase, monthly comparison, spending total, "
        "largest category, recurring payments, unusual purchases, or saving money. "
        "Financial calculations stay local."
    )
