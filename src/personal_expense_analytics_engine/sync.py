"""Incremental sync of a Monobank account into SQLite."""

import math
import sqlite3
import time

from personal_expense_analytics_engine.monobank import MonobankClient
from personal_expense_analytics_engine.monobank_history import fetch_history
from personal_expense_analytics_engine.monobank_mapper import monobank_item_to_transaction
from personal_expense_analytics_engine.storage import (
    delete_pending_since,
    latest_booked_at,
    upsert_transaction,
)

DAY = 24 * 3600


def sync_account(
    conn: sqlite3.Connection,
    client: MonobankClient,
    account_id: str,
    currency: str,
    *,
    initial_days: int = 90,
    overlap_days: int = 7,
    pause: float = 61,
    now: int | None = None,
) -> int:
    """Fetch new data for one account and store it. Returns the number of items fetched.

    First run: loads `initial_days` of history.
    Later runs: re-fetches from `overlap_days` before the newest stored row, so pending
    (hold) rows are replaced by their settled versions.
    """
    now = now if now is not None else int(time.time())
    latest = latest_booked_at(conn, account_id)
    start = now - initial_days * DAY if latest is None else latest - overlap_days * DAY
    days = max(1, math.ceil((now - start) / DAY))
    window_start = now - days * DAY

    # Fetch first: if the API fails, the database stays untouched.
    items = fetch_history(client, days, account=account_id, pause=pause, now=now)

    with conn:  # one atomic transaction
        delete_pending_since(conn, account_id, window_start)
        for item in items:
            txn = monobank_item_to_transaction(item, account_currency=currency)
            upsert_transaction(
                conn,
                txn,
                account_id=account_id,
                booked_at=item["time"],
                is_pending=bool(item.get("hold")),
                mcc=item.get("mcc"),
            )
    return len(items)
