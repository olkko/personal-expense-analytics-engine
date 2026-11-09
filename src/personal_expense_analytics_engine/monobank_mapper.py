"""Convert Monobank statement items into Transaction objects."""

from datetime import datetime, timezone, tzinfo

from personal_expense_analytics_engine.models import Transaction, TransactionDirection


def monobank_item_to_transaction(
    item: dict,
    account_currency: str,
    tz: tzinfo = timezone.utc,
) -> Transaction:
    """Map one item from /personal/statement to a Transaction.

    `amount` is in the ACCOUNT currency (minor units), so the currency we store is
    the account's currency. It has no default on purpose: the caller must say which
    account the item came from. The item's `currencyCode` / `operationAmount` describe
    the original transaction currency and are ignored for now.
    Negative amounts are debits (expenses), positive are credits.
    """
    direction = (
        TransactionDirection.DEBIT
        if item["amount"] < 0
        else TransactionDirection.CREDIT
    )

    return Transaction(
        transaction_id=item["id"],
        date=datetime.fromtimestamp(item["time"], tz=tz).date(),
        description=item["description"],
        amount_minor=abs(item["amount"]),
        currency=account_currency,
        merchant=item.get("counterName"),
        direction=direction,
    )


def monobank_statement_to_transactions(
    items: list[dict],
    account_currency: str,
    tz: tzinfo = timezone.utc,
) -> list[Transaction]:
    """Map a list of statement items to Transaction objects."""
    return [monobank_item_to_transaction(i, account_currency, tz) for i in items]