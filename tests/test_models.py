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
        amount_minor=4250,
        currency="pln",
        category="Groceries",
    )

    assert transaction.date == date(2026, 10, 14)
    assert transaction.amount == Decimal("42.50")
    assert transaction.amount_minor == 4250
    assert transaction.currency == "PLN"
    assert transaction.is_expense is True
    assert transaction.signed_amount_minor == -4250
    assert transaction.signed_amount == Decimal("-42.50")
    assert transaction.category == "Groceries"


def test_transaction_normalizes_text_and_uses_slots():
    """Normalize text fields and keep transactions slotted."""
    transaction = Transaction(
        transaction_id=" txn-005 ",
        date=date(2026, 10, 18),
        description="  Coffee  ",
        amount_minor=500,
        category="  Dining  ",
        merchant="  Cafe  ",
    )

    assert transaction.transaction_id == "txn-005"
    assert transaction.description == "Coffee"
    assert transaction.category == "Dining"
    assert transaction.merchant == "Cafe"
    assert not hasattr(transaction, "__dict__")


@pytest.mark.parametrize("amount_minor", [1.5, Decimal("1.5"), True])
def test_transaction_rejects_non_integer_amount_minor(amount_minor):
    """Reject amounts that are not plain ints (floats, Decimals, bools)."""
    with pytest.raises(TypeError, match="int"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount_minor=amount_minor,
        )


def test_transaction_rejects_negative_amount_minor():
    """Reject negative amounts; direction carries the sign."""
    with pytest.raises(ValueError, match="negative"):
        Transaction(
            transaction_id="txn-002",
            date=date(2026, 10, 15),
            description="Invalid payment",
            amount_minor=-1,
        )


def test_transaction_handles_credit_direction():
    """Preserve positive signed amounts for credit transactions."""
    transaction = Transaction(
        transaction_id="txn-003",
        date=date(2026, 10, 16),
        description="Salary",
        amount_minor=300000,
        direction=TransactionDirection.CREDIT,
        category="Income",
    )

    assert transaction.is_expense is False
    assert transaction.signed_amount_minor == 300000
    assert transaction.amount == Decimal("3000")
    assert transaction.signed_amount == Decimal("3000")
    assert transaction.category == "Income"


def test_transaction_rejects_invalid_currency():
    """Reject currency values that are not three-letter codes."""
    with pytest.raises(ValueError, match="three-letter"):
        Transaction(
            transaction_id="txn-004",
            date=date(2026, 10, 17),
            description="Payment",
            amount_minor=1000,
            currency="PL",
        )


@pytest.mark.parametrize(
    ("currency", "amount_minor", "expected_amount"),
    [
        ("JPY", 123, Decimal("123")),
        ("KWD", 1234, Decimal("1.234")),
    ],
)
def test_transaction_amount_uses_currency_minor_unit_precision(
    currency, amount_minor, expected_amount
):
    """Convert minor units using the exponent defined for the currency."""
    transaction = Transaction(
        transaction_id="txn-currency",
        date=date(2026, 10, 30),
        description="Currency precision check",
        amount_minor=amount_minor,
        currency=currency,
    )

    assert transaction.amount == expected_amount
