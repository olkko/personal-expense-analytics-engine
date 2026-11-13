"""Convert Enable Banking transactions into the shared application model."""

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from babel.numbers import get_currency_precision

from personal_expense_analytics_engine.models import (
    Transaction,
    TransactionDirection,
)


def enablebanking_item_to_transaction(
    item: dict[str, Any],
    account_uid: str,
) -> Transaction:
    """Map one Enable Banking transaction to a normalized transaction.

    Args:
        item (dict[str, Any]): Raw transaction record from Enable Banking.
        account_uid (str): Identifier of the account that owns the transaction.

    Returns:
        Transaction: Normalized transaction with amount stored in minor units.

    Raises:
        ValueError: If required transaction data is missing or invalid.
    """
    amount_data = item.get("transaction_amount")
    if not isinstance(amount_data, dict):
        raise ValueError("Enable Banking transaction is missing transaction_amount.")

    currency = amount_data.get("currency")
    amount_text = amount_data.get("amount")
    if not isinstance(currency, str) or not isinstance(amount_text, str):
        raise ValueError("Enable Banking transaction amount is incomplete.")

    try:
        amount = Decimal(amount_text)
        exponent = get_currency_precision(currency.upper())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enable Banking transaction has an invalid amount or currency.") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Enable Banking transaction amount must be finite and non-negative.")

    amount_minor = amount * (10**exponent)
    if amount_minor != amount_minor.to_integral_value():
        raise ValueError(
            f"Amount {amount_text} has more decimal places than {currency} supports."
        )

    indicator = item.get("credit_debit_indicator")
    if indicator == "DBIT":
        direction = TransactionDirection.DEBIT
    elif indicator == "CRDT":
        direction = TransactionDirection.CREDIT
    else:
        raise ValueError("Enable Banking transaction has an unsupported debit/credit indicator.")

    transaction_date = (
        item.get("booking_date")
        or item.get("value_date")
        or item.get("transaction_date")
    )
    if not isinstance(transaction_date, str):
        raise ValueError("Enable Banking transaction is missing a usable date.")
    try:
        booked_date = date.fromisoformat(transaction_date)
    except ValueError as exc:
        raise ValueError("Enable Banking transaction contains an invalid date.") from exc

    party_field = "creditor" if direction is TransactionDirection.DEBIT else "debtor"
    party = item.get(party_field)
    merchant = party.get("name") if isinstance(party, dict) else None

    remittance = item.get("remittance_information")
    if isinstance(remittance, list):
        description = " ".join(
            part.strip() for part in remittance if isinstance(part, str) and part.strip()
        )
    elif isinstance(remittance, str):
        description = remittance.strip()
    else:
        description = ""
    description = (
        description
        or (merchant.strip() if isinstance(merchant, str) else "")
        or item.get("reference_number")
        or item.get("transaction_id")
        or item.get("entry_reference")
    )
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Enable Banking transaction has no usable description.")

    fingerprint = hashlib.sha256(
        json.dumps(
            {"account_uid": account_uid, "item": item},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    return Transaction(
        transaction_id=f"enablebanking:{fingerprint}",
        date=booked_date,
        description=description,
        amount_minor=int(amount_minor),
        currency=currency,
        merchant=merchant if isinstance(merchant, str) else None,
        direction=direction,
    )
