"""Incremental synchronization of Enable Banking accounts into SQLite."""

import sqlite3
from datetime import date, datetime, time, timezone
from typing import Any, Protocol

from personal_expense_analytics_engine.enablebanking_mapper import (
    enablebanking_item_to_transaction,
)
from personal_expense_analytics_engine.storage import (
    delete_pending_since,
    latest_booked_at,
    upsert_transaction,
)

DAY_SECONDS = 24 * 60 * 60


class TransactionClient(Protocol):
    """Source of normalized Enable Banking transaction responses."""

    def get_transactions(
        self,
        account_uid: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Return transaction records for an account and date range.

        Args:
            account_uid (str): Account identifier.
            date_from (date): Inclusive first transaction date.
            date_to (date): Inclusive last transaction date.

        Returns:
            list[dict[str, Any]]: Raw transaction records from the provider.
        """
        ...


def sync_account(
    conn: sqlite3.Connection,
    client: TransactionClient,
    account_id: str,
    *,
    initial_days: int = 90,
    overlap_days: int = 7,
    now: int | None = None,
) -> int:
    """Fetch and store account transactions; return the number of fetched items.

    The first run loads `initial_days`. Later runs re-fetch from `overlap_days`
    before the newest stored transaction so pending rows can be replaced.

    Args:
        conn (sqlite3.Connection): Database connection to update.
        client (TransactionClient): Provider used to fetch account transactions.
        account_id (str): Account UID used by the provider and local database.
        initial_days (int): History window used when no transactions are stored.
        overlap_days (int): Number of days to re-fetch on subsequent runs.
        now (int | None): Optional current time as a Unix timestamp.

    Returns:
        int: Number of fetched transaction records.

    Raises:
        ValueError: If the initial or overlap day count is invalid.
    """
    if initial_days < 1:
        raise ValueError("initial_days must be at least one.")
    if overlap_days < 0:
        raise ValueError("overlap_days cannot be negative.")
    current_time = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    latest = latest_booked_at(conn, account_id)
    start = (
        current_time - initial_days * DAY_SECONDS
        if latest is None
        else latest - overlap_days * DAY_SECONDS
    )
    date_from = datetime.fromtimestamp(start, timezone.utc).date()
    date_to = datetime.fromtimestamp(current_time, timezone.utc).date()
    window_start = int(
        datetime.combine(date_from, time.min, tzinfo=timezone.utc).timestamp()
    )

    items = client.get_transactions(account_id, date_from, date_to)
    normalized = [
        (item, enablebanking_item_to_transaction(item, account_id))
        for item in items
    ]

    with conn:
        delete_pending_since(conn, account_id, window_start)
        for item, transaction in normalized:
            raw_mcc = item.get("merchant_category_code")
            mcc = (
                int(raw_mcc)
                if isinstance(raw_mcc, (int, str)) and str(raw_mcc).isdigit()
                else None
            )
            booked_at = int(
                datetime.combine(
                    transaction.date,
                    time.min,
                    tzinfo=timezone.utc,
                ).timestamp()
            )
            upsert_transaction(
                conn,
                transaction,
                account_id=account_id,
                booked_at=booked_at,
                is_pending=item.get("status") == "PDNG",
                mcc=mcc,
            )
    return len(normalized)
