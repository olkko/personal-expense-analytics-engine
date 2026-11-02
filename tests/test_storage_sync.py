from personal_expense_analytics_engine.storage import connect, load_transactions, upsert_transaction
from personal_expense_analytics_engine.sync import sync_account

DAY = 86400
NOW = 2_000_000_000


def item(item_id, time, amount=-1000, hold=False, description="Shop"):
    return {
        "id": item_id, "time": time, "description": description,
        "amount": amount, "currencyCode": 980, "mcc": 5411, "hold": hold,
    }


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def statement(self, date_from, date_to, account="0"):
        self.calls.append((date_from, date_to))
        return [i for i in self.items if date_from <= i["time"] <= date_to]


def ids(conn):
    return {r[0] for r in conn.execute("SELECT transaction_id FROM transactions")}


def test_sync_twice_does_not_duplicate():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    assert ids(conn) == {"a"}


def test_pending_row_is_replaced_by_settled_row():
    conn = connect(":memory:")
    client = FakeClient([item("old", NOW - 10 * DAY), item("p1", NOW - DAY, -23341, hold=True)])
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    assert ids(conn) == {"old", "p1"}

    # The hold settles and gets a different id
    client.items = [item("old", NOW - 10 * DAY), item("s1", NOW - DAY, -23500)]
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    assert ids(conn) == {"old", "s1"}


def test_second_sync_is_incremental():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    first_start = client.calls[0][0]
    client.calls.clear()
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    assert client.calls[0][0] > first_start  # asks for a shorter range


def test_category_survives_resync():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - 5 * DAY)])
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    conn.execute("UPDATE transactions SET category = 'Groceries' WHERE transaction_id = 'a'")
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    assert load_transactions(conn)[0].category == "Groceries"


def test_load_transactions_returns_integer_amounts():
    conn = connect(":memory:")
    client = FakeClient([item("a", NOW - DAY, -23341)])
    sync_account(conn, client, "acc", "UAH", initial_days=30, pause=0, now=NOW)
    txn = load_transactions(conn)[0]
    assert txn.amount_minor == 23341
    assert txn.signed_amount_minor == -23341
