"""Import an Enable Banking JSON export (for example a sandbox snapshot) into SQLite.

The export holds ``accounts[]``, each with ``info`` and raw ``transactions`` in the
same shape the live Enable Banking API returns. Transactions go through the same
mapping and storage path as a live sync, so re-importing a file is idempotent.

Usage, from the project root:

    python -m personal_expense_analytics_engine._import_json \
        PL-Synthetic-Bank-synthetic-1.json --database data/synthetic.db
"""

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from personal_expense_analytics_engine._processing import categorize_stored
from personal_expense_analytics_engine._storage import connect
from personal_expense_analytics_engine._sync import sync_account_range


@dataclass(frozen=True, slots=True)
class ExportedAccount:
    """One account from an export with its raw transaction records."""

    account_id: str
    transactions: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """What one import stored and how the new rows were categorized."""

    accounts: dict[str, int]
    categories: dict[str, int]


def _transaction_date(item: dict[str, Any]) -> date | None:
    """Return the date the mapper would use for a raw record, if it is valid."""
    text = (
        item.get("booking_date")
        or item.get("value_date")
        or item.get("transaction_date")
    )
    if not isinstance(text, str):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _account_id(info: dict[str, Any], index: int) -> str:
    """Pick a stable account identifier from the export's account info."""
    # Same preference as the live sync: identification_hash is stable across
    # consents, so an export and a live sync of one account share an ID.
    for key in ("identification_hash", "uid", "resource_id"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Account #{index + 1} in the export has no usable identifier.")


def load_export(path: str | Path) -> list[ExportedAccount]:
    """Read accounts and raw transactions from an Enable Banking JSON export.

    Args:
        path (str | Path): Export file to read.

    Returns:
        list[ExportedAccount]: Accounts in file order.

    Raises:
        ValueError: If the file is not a recognizable export.
        OSError: If the file cannot be read.
    """
    with open(path, encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        raise ValueError(f"{path} has no 'accounts' list.")

    result: list[ExportedAccount] = []
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            raise ValueError(f"Account #{index + 1} in the export is not an object.")
        info = account.get("info")
        transactions = account.get("transactions", [])
        if not isinstance(info, dict) or not isinstance(transactions, list):
            raise ValueError(
                f"Account #{index + 1} is missing 'info' or 'transactions'."
            )
        records = tuple(item for item in transactions if isinstance(item, dict))
        result.append(ExportedAccount(_account_id(info, index), records))
    return result


class _ExportClient:
    """Serve an exported account's transactions through the sync client interface."""

    def __init__(self, account: ExportedAccount) -> None:
        self._account = account

    def get_transactions(
        self, account_uid: str, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        if account_uid != self._account.account_id:
            return []
        return [
            item
            for item in self._account.transactions
            if (day := _transaction_date(item)) is not None
            and date_from <= day <= date_to
        ]


def import_export(
    conn: sqlite3.Connection, path: str | Path, *, categorize: bool = True
) -> ImportResult:
    """Store every account in an export and optionally apply rule categories.

    Only uncategorized rows are categorized, so manual categories survive a
    re-import. Records without a usable date are skipped.

    Args:
        conn (sqlite3.Connection): Database to update.
        path (str | Path): Export file to read.
        categorize (bool): Whether to run rule-based categorization afterwards.

    Returns:
        ImportResult: Stored record count per account and category counts.

    Raises:
        ValueError: If the export or one of its transactions is invalid.
    """
    stored: dict[str, int] = {}
    for account in load_export(path):
        days = [d for item in account.transactions if (d := _transaction_date(item))]
        if not days:
            stored[account.account_id] = 0
            continue
        stored[account.account_id] = sync_account_range(
            conn, _ExportClient(account), account.account_id, min(days), max(days)
        )
    categories = categorize_stored(conn) if categorize else {}
    return ImportResult(accounts=stored, categories=categories)


def main(argv: list[str] | None = None) -> None:
    """Import an export file and print a short summary.

    Args:
        argv (list[str] | None): Optional command-line arguments for testing.
    """
    parser = argparse.ArgumentParser(
        description="Import an Enable Banking JSON export into SQLite."
    )
    parser.add_argument("export", type=Path, help="Enable Banking JSON export file")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/expenses.db"),
        help="SQLite database path (default: data/expenses.db)",
    )
    parser.add_argument(
        "--no-categorize",
        action="store_true",
        help="Store transactions without applying rule-based categories",
    )
    args = parser.parse_args(argv)
    if not args.export.is_file():
        parser.error(f"export file not found: {args.export}")

    with closing(connect(args.database)) as conn:
        result = import_export(conn, args.export, categorize=not args.no_categorize)

    for account_id, count in result.accounts.items():
        print(f"Imported {count} transactions for account {account_id}")
    print(f"Database: {args.database}")
    if result.categories:
        print("Categorized:")
        for category, count in sorted(result.categories.items()):
            print(f"- {category}: {count}")


if __name__ == "__main__":
    main()
