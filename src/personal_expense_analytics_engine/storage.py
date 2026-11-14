"""SQLite storage for transactions. Money is stored as integer minor units."""

import sqlite3
from datetime import date
from pathlib import Path

from personal_expense_analytics_engine.models import Transaction, TransactionDirection

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL,
    booked_at      INTEGER NOT NULL,          -- unix timestamp from the bank
    date           TEXT NOT NULL,             -- ISO date
    description    TEXT NOT NULL,
    amount_minor   INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency       TEXT NOT NULL,
    direction      TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category       TEXT,
    merchant       TEXT,
    mcc            INTEGER,
    is_pending     INTEGER NOT NULL DEFAULT 0 CHECK (is_pending IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_txn_account_time ON transactions (account_id, booked_at);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions (date);
"""


def connect(path: str | Path = "data/expenses.db") -> sqlite3.Connection:
    """Open (and create if needed) the database and make sure the schema exists.

    Args:
        path (str | Path): Database file path, or ``":memory:"`` for an in-memory DB.

    Returns:
        sqlite3.Connection: Open connection with the application schema initialized.

    Raises:
        OSError: If the database directory cannot be created.
        sqlite3.Error: If the database cannot be opened or initialized.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def upsert_transaction(
    conn: sqlite3.Connection,
    txn: Transaction,
    *,
    account_id: str,
    booked_at: int,
    is_pending: bool = False,
    mcc: int | None = None,
) -> None:
    """Insert or update a transaction. An existing category is never overwritten.

    Args:
        conn (sqlite3.Connection): Database connection to update.
        txn (Transaction): Transaction to insert or update.
        account_id (str): Owning bank account identifier.
        booked_at (int): Booking time as a Unix timestamp.
        is_pending (bool): Whether the transaction is pending.
        mcc (int | None): Optional merchant category code.

    Raises:
        sqlite3.Error: If the database operation fails.
    """
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_id, account_id, booked_at, date, description, amount_minor,
            currency, direction, category, merchant, mcc, is_pending
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(transaction_id) DO UPDATE SET
            account_id   = excluded.account_id,
            booked_at    = excluded.booked_at,
            date         = excluded.date,
            description  = excluded.description,
            amount_minor = excluded.amount_minor,
            currency     = excluded.currency,
            direction    = excluded.direction,
            merchant     = excluded.merchant,
            mcc          = excluded.mcc,
            is_pending   = excluded.is_pending,
            category     = COALESCE(transactions.category, excluded.category)
        """,
        (
            txn.transaction_id,
            account_id,
            booked_at,
            txn.date.isoformat(),
            txn.description,
            txn.amount_minor,
            txn.currency,
            txn.direction.value,
            txn.category,
            txn.merchant,
            mcc,
            int(is_pending),
        ),
    )


def delete_pending_since(
    conn: sqlite3.Connection,
    account_id: str,
    since: int,
    until: int | None = None,
) -> int:
    """Delete pending rows booked at or after `since`.

    Args:
        conn (sqlite3.Connection): Database connection to update.
        account_id (str): Account whose pending rows should be deleted.
        since (int): Inclusive lower bound as a Unix timestamp.
        until (int | None): Optional exclusive upper bound as a Unix timestamp.

    Returns:
        int: Number of deleted rows.

    Raises:
        sqlite3.Error: If the database operation fails.
    """
    if until is None:
        cur = conn.execute(
            "DELETE FROM transactions "
            "WHERE account_id = ? AND is_pending = 1 AND booked_at >= ?",
            (account_id, since),
        )
    else:
        cur = conn.execute(
            "DELETE FROM transactions "
            "WHERE account_id = ? AND is_pending = 1 "
            "AND booked_at >= ? AND booked_at < ?",
            (account_id, since, until),
        )
    return cur.rowcount


def latest_booked_at(conn: sqlite3.Connection, account_id: str) -> int | None:
    """Return the newest booking timestamp stored for an account.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str): Account whose newest booking time is requested.

    Returns:
        int | None: Newest Unix timestamp, or None if the account has no rows.

    Raises:
        sqlite3.Error: If the database query fails.
    """
    row = conn.execute(
        "SELECT MAX(booked_at) FROM transactions WHERE account_id = ?", (account_id,)
    ).fetchone()
    return row[0]


def load_transactions(conn: sqlite3.Connection, account_id: str | None = None) -> list[Transaction]:
    """Load transactions (newest first) as Transaction objects.

    Args:
        conn (sqlite3.Connection): Database connection to query.
        account_id (str | None): Optional account filter.

    Returns:
        list[Transaction]: Matching transactions ordered newest first.

    Raises:
        sqlite3.Error: If the database query fails.
        ValueError: If a stored date or direction is invalid.
    """
    query = (
        "SELECT transaction_id, date, description, amount_minor, currency, category, "
        "merchant, direction FROM transactions"
    )
    params: tuple = ()
    if account_id is not None:
        query += " WHERE account_id = ?"
        params = (account_id,)
    query += " ORDER BY booked_at DESC"
    return [
        Transaction(
            transaction_id=r[0],
            date=date.fromisoformat(r[1]),
            description=r[2],
            amount_minor=r[3],
            currency=r[4],
            category=r[5],
            merchant=r[6],
            direction=TransactionDirection(r[7]),
        )
        for r in conn.execute(query, params)
    ]
