import math
from datetime import date

import pytest

from personal_expense_analytics_engine._dashboard_data import (
    OTHER_CATEGORY,
    accounts,
    category_changes,
    category_frame,
    category_order,
    currencies,
    fold_categories,
    monthly_frame,
    recurring_frame,
    to_major,
    transactions_frame,
)
from personal_expense_analytics_engine._models import (
    Transaction,
    TransactionDirection,
)
from personal_expense_analytics_engine._storage import connect, upsert_transaction

DEBIT = TransactionDirection.DEBIT
CREDIT = TransactionDirection.CREDIT


def add(
    conn,
    tid,
    day,
    amount_minor,
    *,
    category=None,
    direction=DEBIT,
    currency="UAH",
    description=None,
    account="acc",
):
    upsert_transaction(
        conn,
        Transaction(
            transaction_id=tid,
            date=day,
            description=description or f"Item {tid}",
            amount_minor=amount_minor,
            currency=currency,
            category=category,
            direction=direction,
        ),
        account_id=account,
        booked_at=day.toordinal() * 86400,
    )


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    yield connection
    connection.close()


def test_to_major_uses_currency_precision():
    assert to_major(4250, "PLN") == 42.5
    assert to_major(500, "JPY") == 500


def test_currencies_and_accounts(conn):
    add(conn, "a", date(2026, 9, 1), 100, currency="UAH")
    add(conn, "b", date(2026, 9, 2), 100, currency="UAH", account="second")
    add(conn, "c", date(2026, 9, 3), 100, currency="EUR")
    assert currencies(conn) == ["UAH", "EUR"]
    assert currencies(conn, "second") == ["UAH"]
    assert accounts(conn) == ["acc", "second"]


def test_monthly_frame_excludes_transfers_and_flags_partial_months(conn):
    add(conn, "1", date(2026, 8, 15), 10_000, category="Groceries")
    add(conn, "2", date(2026, 9, 1), 20_000, category="Groceries")
    add(conn, "3", date(2026, 9, 2), 99_900, category="Transfer")
    add(conn, "4", date(2026, 9, 3), 50_000, category="Income", direction=CREDIT)
    add(conn, "5", date(2026, 9, 4), 7_000, currency="EUR", category="Dining")

    frame = monthly_frame(conn, "UAH", today=date(2026, 9, 20))

    assert list(frame["month"]) == ["2026-08", "2026-09"]
    september = frame.set_index("month").loc["2026-09"]
    assert september["spent"] == 200.0
    assert september["income"] == 500.0
    assert september["net"] == 300.0
    # August starts mid-month; September is the current month.
    assert list(frame["partial"]) == [True, True]


def test_monthly_frame_is_empty_for_unknown_currency(conn):
    add(conn, "1", date(2026, 9, 1), 100, category="Groceries")
    frame = monthly_frame(conn, "USD", today=date(2026, 10, 1))
    assert frame.empty
    assert list(frame.columns) == ["month", "spent", "income", "net", "partial"]


def test_category_order_ranks_by_total_and_fold_keeps_largest(conn):
    amounts = {"A": 900, "B": 800, "C": 300, "D": 200, "E": 100}
    for index, (category, amount) in enumerate(amounts.items()):
        add(conn, f"t{index}", date(2026, 9, 1), amount, category=category)
    frame = category_frame(conn, "UAH")

    order = category_order(frame)
    assert order == ["A", "B", "C", "D", "E"]

    folded = fold_categories(frame, order, keep=2)
    totals = dict(zip(folded["category"], folded["spent"]))
    assert set(totals) == {"A", "B", OTHER_CATEGORY}
    assert totals[OTHER_CATEGORY] == pytest.approx(6.0)


def test_fold_categories_does_not_fold_a_single_extra_category(conn):
    for index, category in enumerate("ABC"):
        add(conn, f"t{index}", date(2026, 9, 1), 100, category=category)
    frame = category_frame(conn, "UAH")
    folded = fold_categories(frame, category_order(frame), keep=2)
    assert OTHER_CATEGORY not in set(folded["category"])


def test_category_changes_handles_new_and_dropped_categories(conn):
    add(conn, "1", date(2026, 8, 5), 10_000, category="Groceries")
    add(conn, "2", date(2026, 8, 6), 5_000, category="Dining")
    add(conn, "3", date(2026, 9, 5), 15_000, category="Groceries")
    add(conn, "4", date(2026, 9, 6), 2_000, category="Pets")

    changes = category_changes(
        category_frame(conn, "UAH"), "2026-09", "2026-08"
    ).set_index("category")

    assert changes.loc["Groceries", "change"] == 50.0
    assert changes.loc["Groceries", "change_pct"] == 50.0
    assert changes.loc["Dining", "spent"] == 0.0
    assert changes.loc["Dining", "change_pct"] == -100.0
    assert math.isnan(changes.loc["Pets", "change_pct"])


def test_transactions_frame_signs_amounts_and_marks_spending(conn):
    add(conn, "1", date(2026, 9, 1), 1_000, category="Groceries")
    add(conn, "2", date(2026, 9, 2), 2_000, category="Transfer")
    add(conn, "3", date(2026, 9, 3), 3_000, category="Income", direction=CREDIT)

    frame = transactions_frame(conn, "UAH").set_index("description")

    assert frame.loc["Item 1", "amount"] == -10.0
    assert frame.loc["Item 3", "amount"] == 30.0
    assert frame["counts_as_spending"].to_dict() == {
        "Item 3": False,
        "Item 2": False,
        "Item 1": True,
    }


def test_recurring_frame_estimates_monthly_cost(conn):
    for index, day in enumerate(
        [date(2026, 7, 1), date(2026, 7, 31), date(2026, 8, 30)]
    ):
        add(
            conn,
            f"n{index}",
            day,
            19_900,
            category="Subscriptions",
            description="Netflix",
        )
    frame = recurring_frame(conn, "UAH")
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["merchant"] == "netflix"
    assert row["frequency"] == "monthly"
    assert row["per_month"] == pytest.approx(199.0)
    assert row["next_expected"] == date(2026, 9, 29)
