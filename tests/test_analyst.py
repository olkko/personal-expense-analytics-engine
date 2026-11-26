from datetime import date
from unittest.mock import patch

import pytest

from personal_expense_analytics_engine._analyst import (
    monthly_spending_insight,
    question_insight,
)
from personal_expense_analytics_engine._models import Transaction
from personal_expense_analytics_engine._storage import connect, upsert_transaction


def add_transaction(
    conn,
    transaction_id: str,
    day: date,
    amount_minor: int,
    category: str,
    *,
    merchant: str | None = None,
    currency: str = "PLN",
) -> None:
    transaction = Transaction(
        transaction_id=transaction_id,
        date=day,
        description=merchant or category,
        amount_minor=amount_minor,
        currency=currency,
        category=category,
        merchant=merchant,
    )
    upsert_transaction(
        conn,
        transaction,
        account_id="account-1",
        booked_at=day.toordinal() * 86400,
    )


def test_question_biggest_purchase_is_answered_locally():
    connection = connect(":memory:")
    add_transaction(connection, "small", date(2026, 11, 2), 1200, "Dining")
    add_transaction(connection, "large", date(2026, 11, 3), 9900, "Shopping")

    with patch("personal_expense_analytics_engine._analyst.generate_narrative") as generate:
        answer = question_insight(
            connection, "What was my biggest purchase?", "2026-11", "PLN"
        )

    assert "99.00 PLN" in answer.text
    assert answer.model is None
    generate.assert_not_called()


def test_question_saving_advice_sends_no_numbers_or_transaction_details():
    connection = connect(":memory:")
    add_transaction(
        connection,
        "coffee-123",
        date(2026, 11, 2),
        1200,
        "Dining",
        merchant="Secret Cafe",
    )
    add_transaction(connection, "shop-456", date(2026, 11, 3), 9900, "Shopping")

    with patch(
        "personal_expense_analytics_engine._analyst.generate_narrative",
        return_value="Review your largest categories.",
    ) as generate:
        answer = question_insight(
            connection, "How can I save money?", "2026-11", "PLN"
        )

    evidence = generate.call_args.args[0]
    assert answer.text == "Review your largest categories."
    assert evidence == "The largest spending categories for the selected period are: Shopping, Dining. No recurring payment pattern was detected."
    assert not any(character.isdigit() for character in evidence)
    assert "Secret Cafe" not in evidence
    assert "coffee-123" not in evidence


def test_question_compares_two_months_locally():
    connection = connect(":memory:")
    add_transaction(connection, "old", date(2026, 10, 2), 1200, "Dining")
    add_transaction(connection, "new", date(2026, 11, 2), 2500, "Dining")

    with patch("personal_expense_analytics_engine._analyst.generate_narrative") as generate:
        answer = question_insight(
            connection,
            "Compare 2026-10 and 2026-11",
            "2026-11",
            "PLN",
        )

    assert "12.00 PLN" in answer.text
    assert "25.00 PLN" in answer.text
    assert "13.00 PLN" in answer.text
    assert answer.model is None
    generate.assert_not_called()


def test_monthly_insight_calculates_change_and_category_drivers():
    conn = connect(":memory:")
    previous_items = [
        ("old-dining", "Dining", 10000),
        ("old-shopping", "Shopping", 15000),
        ("old-transport", "Transport", 5000),
        ("old-groceries", "Groceries", 50000),
    ]
    current_items = [
        ("new-dining", "Dining", 12400),
        ("new-shopping", "Shopping", 19650),
        ("new-transport", "Transport", 5950),
        ("new-groceries", "Groceries", 50000),
    ]
    for transaction_id, category, amount in previous_items:
        add_transaction(conn, transaction_id, date(2026, 10, 10), amount, category)
    for transaction_id, category, amount in current_items:
        add_transaction(conn, transaction_id, date(2026, 11, 10), amount, category)

    with patch(
        "personal_expense_analytics_engine._analyst.generate_narrative",
        return_value="Generated locally by Ollama.",
    ) as generate:
        insight = monthly_spending_insight(
            conn,
            "2026-11",
            "pln",
            today=date(2026, 12, 1),
        )

    assert insight.spent_minor == 88000
    assert insight.previous_spent_minor == 80000
    assert insight.change_pct == 10.0
    assert [(item.category, item.change_minor) for item in insight.categories] == [
        ("Shopping", 4650),
        ("Dining", 2400),
        ("Transport", 950),
    ]
    assert insight.text == "Generated locally by Ollama."
    assert "Shopping: +46.50 PLN (31% increase)" in insight.facts_text
    model_evidence = generate.call_args.args[0]
    assert "Spending increased compared with the previous period." in model_evidence
    assert "Shopping" in model_evidence
    assert not any(character.isdigit() for character in model_evidence)
    assert generate.call_args.kwargs["model"] == "qwen3:0.6b"


def test_monthly_insight_does_not_claim_comparison_without_baseline():
    conn = connect(":memory:")
    add_transaction(
        conn,
        "only-month",
        date(2026, 11, 10),
        1234,
        "Dining",
    )

    with patch(
        "personal_expense_analytics_engine._analyst.generate_narrative",
        return_value="Generated locally by Ollama.",
    ):
        insight = monthly_spending_insight(
            conn,
            "2026-11",
            "PLN",
            today=date(2026, 12, 1),
        )

    assert insight.previous_spent_minor is None
    assert insight.change_pct is None
    assert "no comparable spending data" in insight.facts_text
    assert "2026-11 may be incomplete" in insight.facts_text


def test_monthly_insight_includes_detected_unusual_purchase():
    conn = connect(":memory:")
    for index, month in enumerate(range(6, 11), start=1):
        add_transaction(
            conn,
            f"regular-{index}",
            date(2026, month, 10),
            1000,
            "Dining",
            merchant="Cafe Example",
        )
    add_transaction(
        conn,
        "large-current",
        date(2026, 11, 10),
        10000,
        "Dining",
        merchant="Cafe Example",
    )

    with patch(
        "personal_expense_analytics_engine._analyst.generate_narrative",
        return_value="Generated locally by Ollama.",
    ):
        insight = monthly_spending_insight(
            conn,
            "2026-11",
            "PLN",
            today=date(2026, 12, 1),
        )

    assert [item.transaction_id for item in insight.unusual_transactions] == [
        "large-current"
    ]
    assert insight.categories[0].unusual_count == 1
    assert "including 1 unusually large purchase" in insight.facts_text


def test_monthly_insight_requires_month_data_for_requested_currency():
    conn = connect(":memory:")
    add_transaction(conn, "pln", date(2026, 11, 10), 1000, "Dining")

    with pytest.raises(ValueError, match="No spending data"):
        monthly_spending_insight(conn, "2026-11", "EUR")
