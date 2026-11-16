"""Detect probable duplicate transactions. Report only: nothing is ever deleted."""

import sqlite3


def find_probable_duplicates(
    conn: sqlite3.Connection, window_seconds: int = 120
) -> list[list[str]]:
    """Group transactions with the same account, amount, currency, direction and
    description that were booked within `window_seconds` of each other.

    Args:
        conn (sqlite3.Connection): Database connection containing transactions.
        window_seconds (int): Maximum allowed time between likely duplicates.

    Returns:
        list[list[str]]: Groups of transaction IDs that may be duplicates.
    """
    rows = conn.execute(
        """
        SELECT transaction_id, account_id, amount_minor, currency, direction,
               description, booked_at
        FROM transactions
        ORDER BY account_id, amount_minor, currency, direction, description, booked_at
        """
    ).fetchall()

    groups: list[list[str]] = []
    current: list[tuple] = []
    for row in rows:
        same_key = current and row[1:6] == current[-1][1:6]
        close = same_key and row[6] - current[-1][6] <= window_seconds
        if close:
            current.append(row)
        else:
            if len(current) > 1:
                groups.append([r[0] for r in current])
            current = [row]
    if len(current) > 1:
        groups.append([r[0] for r in current])
    return groups
