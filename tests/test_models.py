from datetime import date
from decimal import Decimal

import pytest

from personal_expense_analytics_engine.models import (
    Category,
    CategoryType,
    Transaction,
    TransactionDirection,
)


def test_category_stores_name_and_type():
    category = Category(" Groceries ", CategoryType.VARIABLE)

    assert category.name == "Groceries"
    assert category.category_type is CategoryType.VARIABLE


def test_transaction_sets_expected_values():
    transaction = Transaction(
        transaction_id="txn-001",
        date=date(2026, 10, 14),
        description="Tesco supermarket",
        amount=Decimal("42.50"),
        currency="pln",
        category="Groceries",
    )

    assert transaction.date == date(2026, 10, 14)
    assert transaction.amount == Decimal("42.50")
    assert transaction.currency == "PLN"
    assert transaction.is_expense is True
    assert transaction.signed_amount == Decimal("-42.50")
    assert transaction.category == "Groceries"


def test_transaction_rejects_non_decimal_amount():
    with pytest.raises(TypeError, match="Decimal"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount="not-a-number",
        )


def test_transaction_rejects_non_finite_amount():
    with pytest.raises(ValueError, match="finite"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount=Decimal("NaN"),
        )


def test_transaction_handles_credit_direction():
    transaction = Transaction(
        transaction_id="txn-003",
        date=date(2026, 10, 16),
        description="Salary",
        amount=Decimal("3000"),
        direction=TransactionDirection.CREDIT,
        category="Income",
    )

    assert transaction.is_expense is False
    assert transaction.signed_amount == Decimal("3000")
    assert transaction.category == "Income"


def test_transaction_rejects_invalid_currency():
    with pytest.raises(ValueError, match="three-letter"):
        Transaction(
            transaction_id="txn-004",
            date=date(2026, 10, 17),
            description="Payment",
            amount=Decimal("10"),
            currency="PL",
        )
