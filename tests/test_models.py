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
    """Store a trimmed category name and its category type."""
    category = Category(" Groceries ", CategoryType.VARIABLE)

    assert category.name == "Groceries"
    assert category.category_type is CategoryType.VARIABLE


def test_transaction_sets_expected_values():
    """Normalize transaction values and calculate its debit amount."""
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


def test_transaction_normalizes_text_and_uses_slots():
    """Normalize text fields and keep transactions slotted."""
    transaction = Transaction(
        transaction_id=" txn-005 ",
        date=date(2026, 10, 18),
        description="  Coffee  ",
        amount=Decimal("5.00"),
        category="  Dining  ",
        merchant="  Cafe  ",
    )

    assert transaction.transaction_id == "txn-005"
    assert transaction.description == "Coffee"
    assert transaction.category == "Dining"
    assert transaction.merchant == "Cafe"
    assert not hasattr(transaction, "__dict__")


def test_transaction_rejects_non_decimal_amount():
    """Reject transaction amounts that are not Decimal instances."""
    with pytest.raises(TypeError, match="Decimal"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount="not-a-number",
        )


def test_transaction_rejects_non_finite_amount():
    """Reject Decimal amounts that are not finite."""
    with pytest.raises(ValueError, match="finite"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount=Decimal("NaN"),
        )


def test_transaction_handles_credit_direction():
    """Preserve positive signed amounts for credit transactions."""
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
    """Reject currency values that are not three-letter codes."""
    with pytest.raises(ValueError, match="three-letter"):
        Transaction(
            transaction_id="txn-004",
            date=date(2026, 10, 17),
            description="Payment",
            amount=Decimal("10"),
            currency="PL",
        )
