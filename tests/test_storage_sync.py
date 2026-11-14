from datetime import date, datetime, timezone
from decimal import Decimal

from personal_expense_analytics_engine.models import Transaction
from personal_expense_analytics_engine.storage import (
    connect,
    load_transactions,
    upsert_transaction,
)
from personal_expense_analytics_engine.sync import sync_account, sync_account_range

DAY = 86400
NOW = 2_000_000_000


def item(item_id, timestamp, amount_minor=-1000, hold=False, description="Shop"):
    return {
        "entry_reference": item_id,
        "transaction_id": item_id,
        "booking_date": datetime.fromtimestamp(timestamp, timezone.utc)
        .date()
        .isoformat(),
        "transaction_amount": {
            "amount": str(Decimal(abs(amount_minor)) / Decimal(100)),
            "currency": "UAH",
        },
        "credit_debit_indicator": "DBIT" if amount_minor < 0 else "CRDT",
        "status": "PDNG" if hold else "BOOK",
        "merchant_category_code": "5411",
        "creditor": {"name": description},
        "remittance_information": [description],
    }


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_transactions(self, account_uid, date_from, date_to):
        self.calls.append((date_from, date_to))
        return [
            item
            for item in self.items
            if date_from.isoformat() <= item["booking_date"] <= date_to.isoformat()
        ]


def descriptions(conn):
    return {r[0] for r in conn.execute("SELECT description FROM transactions")}


def test_sync_twice_does_not_duplicate():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    assert descriptions(conn) == {"Shop"}


def test_pending_row_is_replaced_by_settled_row():
    conn = connect(":memory:")
    client = FakeClient([item("old", NOW - 10 * DAY), item("p1", NOW - DAY, -23341, hold=True)])
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2

    # The hold settles and gets a different provider transaction ID.
    client.items = [item("old", NOW - 10 * DAY), item("s1", NOW - DAY, -23500)]
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2
    assert conn.execute(
        "SELECT amount_minor FROM transactions WHERE description = 'Shop' "
        "ORDER BY amount_minor DESC LIMIT 1"
    ).fetchone() == (23500,)


def test_second_sync_is_incremental():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    first_start = client.calls[0][0]
    client.calls.clear()
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    assert client.calls[0][0] > first_start  # asks for a shorter range


def test_category_survives_resync():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    transaction_id = conn.execute(
        "SELECT transaction_id FROM transactions"
    ).fetchone()[0]
    conn.execute(
        "UPDATE transactions SET category = 'Groceries' WHERE transaction_id = ?",
        (transaction_id,),
    )
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    assert load_transactions(conn)[0].category == "Groceries"


def test_load_transactions_returns_integer_amounts():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - DAY, -23341)])
    sync_account(conn, client, "acc", initial_days=30, now=NOW)
    txn = load_transactions(conn)[0]
    assert txn.amount_minor == 23341
    assert txn.signed_amount_minor == -23341


def test_explicit_sync_range_deletes_only_pending_rows_inside_the_range():
    conn = connect(":memory:")
    inside = Transaction(
        transaction_id="pending-inside",
        date=date(2020, 9, 15),
        description="Pending inside",
        amount_minor=1000,
    )
    outside = Transaction(
        transaction_id="pending-outside",
        date=date(2020, 10, 1),
        description="Pending outside",
        amount_minor=1000,
    )
    with conn:
        upsert_transaction(
            conn,
            inside,
            account_id="acc",
            booked_at=int(datetime(2020, 9, 15, tzinfo=timezone.utc).timestamp()),
            is_pending=True,
        )
        upsert_transaction(
            conn,
            outside,
            account_id="acc",
            booked_at=int(datetime(2020, 10, 1, tzinfo=timezone.utc).timestamp()),
            is_pending=True,
        )

    sync_account_range(
        conn,
        FakeClient([]),
        "acc",
        date(2020, 9, 1),
        date(2020, 9, 30),
    )

    remaining = {
        row[0]
        for row in conn.execute(
            "SELECT transaction_id FROM transactions WHERE is_pending = 1"
        )
    }
    assert remaining == {"pending-outside"}
