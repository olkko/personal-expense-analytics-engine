from datetime import date
from decimal import Decimal

import pytest

from personal_expense_analytics_engine.models import TransactionDirection
from personal_expense_analytics_engine.monobank_mapper import monobank_item_to_transaction


def make_item(**overrides):
    item = {
        "id": "abc123",
        "time": 1760000000,  # 2025-10-09 UTC
        "description": "Silpo",
        "mcc": 5411,
        "amount": -12550,
        "operationAmount": -12550,
        "currencyCode": 980,
        "balance": 100000,
    }
    item.update(overrides)
    return item


def convert(item, currency="UAH"):
    return monobank_item_to_transaction(item, account_currency=currency)


def test_expense_maps_to_debit():
    txn = convert(make_item())
    assert txn.transaction_id == "abc123"
    assert txn.amount_minor == 12550
    assert txn.currency == "UAH"
    assert txn.direction == TransactionDirection.DEBIT
    assert txn.signed_amount_minor == -12550
    assert txn.signed_amount == Decimal("-125.50")
    assert txn.date == date(2025, 10, 9)


def test_income_maps_to_credit():
    txn = convert(make_item(amount=500000))
    assert txn.direction == TransactionDirection.CREDIT
    assert txn.signed_amount_minor == 500000


def test_counter_name_becomes_merchant():
    txn = convert(make_item(counterName="Jan Kowalski"))
    assert txn.merchant == "Jan Kowalski"


def test_foreign_currency_uses_account_amount_and_currency():
    # Paid 20.00 PLN, charged 233.41 UAH to a UAH account
    txn = convert(make_item(amount=-23341, operationAmount=-2000, currencyCode=985))
    assert txn.amount_minor == 23341
    assert txn.currency == "UAH"


def test_currency_is_taken_from_the_account_argument():
    assert convert(make_item(), currency="PLN").currency == "PLN"


def test_invalid_account_currency_raises():
    with pytest.raises(ValueError):
        convert(make_item(), currency="XX")
