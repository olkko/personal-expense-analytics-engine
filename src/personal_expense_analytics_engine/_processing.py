"""Transaction processing on stored data: categorization and recurring payments."""

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass

from personal_expense_analytics_engine._categorization import (
    CATEGORY_NAMES,
    categorize,
)
from personal_expense_analytics_engine._models import TransactionDirection
from personal_expense_analytics_engine._ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_CATEGORY_CONFIDENCE,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    CategoryPrediction,
    categorize_transactions,
)
from personal_expense_analytics_engine._recurring import (
    RecurringPayment,
    detect_recurring,
)
from personal_expense_analytics_engine._storage import (
    load_transactions,
    rows_for_categorization,
    rows_for_uncategorized_explanation,
    set_category,
)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredCategoryExplanation:
    """Non-mutating rule and AI explanation for an uncategorized row."""

    date: str
    description: str
    merchant: str | None
    mcc: int | None
    direction: str
    rule_category: str | None
    ai_prediction: CategoryPrediction | None


def categorize_stored(
    conn: sqlite3.Connection, *, overwrite: bool = False
) -> dict[str, int]:
    """Categorize stored transactions and return a count per category.

    By default only rows without a category are touched, so manual categories survive.
    overwrite=True recomputes everything (and replaces manual categories).

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        overwrite (bool): Whether to replace existing categories.

    Returns:
        dict[str, int]: Number of categorized transactions for each category.
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


def categorize_stored_with_ollama(
    conn: sqlite3.Connection,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    min_confidence: float = DEFAULT_CATEGORY_CONFIDENCE,
) -> dict[str, int]:
    """Apply rules first, then locally classify remaining transactions with Ollama.

    Only rows without an existing category are processed. Rule-based categories are
    preserved, and AI predictions are saved only when they use an allowed category
    and meet the confidence threshold.

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        model (str): Installed local Ollama model name.
        base_url (str): Local Ollama HTTP API origin.
        timeout (float): Maximum time to wait for Ollama.
        min_confidence (float): Minimum accepted model confidence, from 0 to 1.

    Returns:
        dict[str, int]: Number of rows assigned to each category, including rows
            left Uncategorized.

    Raises:
        ValueError: If the confidence threshold is outside the range zero to one.
        OllamaAnalystError: If Ollama is unavailable or returns invalid predictions.
        sqlite3.Error: If reading or updating the database fails.
    """
    counts = Counter(categorize_stored(conn))
    _logger.info("Rule-based categorization: %s", dict(counts) or "no rows to process")
    rows = rows_for_categorization(conn, only_uncategorized=True)
    if not rows:
        return dict(counts)

    _logger.info(
        "Sending %d remaining transaction(s) to local Ollama; descriptions, "
        "merchants, MCCs, and directions only.",
        len(rows),
    )
    inputs = [
        {
            "description": description,
            "merchant": merchant,
            "mcc": mcc,
            "direction": direction,
        }
        for _transaction_id, description, merchant, mcc, direction in rows
    ]
    predictions = categorize_transactions(
        inputs,
        tuple(sorted(CATEGORY_NAMES)),
        model=model,
        base_url=base_url,
        timeout=timeout,
        min_confidence=min_confidence,
    )
    ai_counts = Counter(
        prediction.category if prediction.accepted else "Uncategorized"
        for prediction in predictions
    )
    _logger.info("Local Ollama results: %s", dict(ai_counts))

    with conn:
        for row, prediction in zip(rows, predictions, strict=True):
            if prediction.accepted:
                set_category(conn, row[0], prediction.category)
                counts[prediction.category] += 1

    remaining_uncategorized = sum(not prediction.accepted for prediction in predictions)
    if remaining_uncategorized:
        counts["Uncategorized"] = remaining_uncategorized
    else:
        counts.pop("Uncategorized", None)
    return dict(counts)


def explain_uncategorized_stored(
    conn: sqlite3.Connection,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    min_confidence: float = DEFAULT_CATEGORY_CONFIDENCE,
) -> list[StoredCategoryExplanation]:
    """Explain uncategorized rows without changing the database.

    Deterministic rules are checked first. Rows without a rule match are sent to
    local Ollama for a category suggestion, confidence, and short explanation.

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        model (str): Installed local Ollama model name.
        base_url (str): Local Ollama HTTP API origin.
        timeout (float): Maximum time to wait for Ollama.
        min_confidence (float): Threshold used to mark a model prediction accepted.

    Returns:
        list[StoredCategoryExplanation]: Explanations in date-descending order.

    Raises:
        ValueError: If the confidence threshold is outside zero to one.
        OllamaAnalystError: If Ollama is unavailable or returns invalid predictions.
        sqlite3.Error: If reading the database fails.
    """
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between zero and one.")

    rows = rows_for_uncategorized_explanation(conn)
    if not rows:
        return []

    rule_categories: list[str | None] = []
    pending_indices: list[int] = []
    for index, (_date, description, merchant, mcc, direction) in enumerate(rows):
        category = categorize(
            description,
            direction=TransactionDirection(direction),
            mcc=mcc,
            merchant=merchant,
        )
        rule_categories.append(category)
        if category is None:
            pending_indices.append(index)

    ai_predictions: dict[int, CategoryPrediction] = {}
    if pending_indices:
        inputs = [
            {
                "description": rows[index][1],
                "merchant": rows[index][2],
                "mcc": rows[index][3],
                "direction": rows[index][4],
            }
            for index in pending_indices
        ]
        predictions = categorize_transactions(
            inputs,
            tuple(sorted(CATEGORY_NAMES)),
            model=model,
            base_url=base_url,
            timeout=timeout,
            min_confidence=min_confidence,
        )
        ai_predictions = dict(zip(pending_indices, predictions, strict=True))

    return [
        StoredCategoryExplanation(
            date=row[0],
            description=row[1],
            merchant=row[2],
            mcc=row[3],
            direction=row[4],
            rule_category=rule_categories[index],
            ai_prediction=ai_predictions.get(index),
        )
        for index, row in enumerate(rows)
    ]


def find_recurring_stored(
    conn: sqlite3.Connection, account_id: str | None = None
) -> list[RecurringPayment]:
    """Detect recurring payments in stored data. Run categorize_stored first.

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        account_id (str | None): Optional account identifier to filter by.

    Returns:
        list[RecurringPayment]: Detected recurring payments.
    """
    return detect_recurring(load_transactions(conn, account_id))
