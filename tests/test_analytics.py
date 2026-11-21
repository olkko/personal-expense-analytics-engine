from datetime import date

from personal_expense_analytics_engine._analytics import (
    MonthlyTotal,
    category_totals,
    detect_unusual,
    format_minor,
    month_over_month,
    monthly_totals,
    partial_months,
)
from personal_expense_analytics_engine._models import (
    Transaction,
    TransactionDirection,
)
from personal_expense_analytics_engine._storage import connect, upsert_transaction

DEBIT = TransactionDirection.DEBIT
CREDIT = TransactionDirection.CREDIT


def add(
    conn, tid, day, amount_minor, *, category=None, direction=DEBIT, currency="UAH"
):
    upsert_transaction(
        conn,
        Transaction(
            transaction_id=tid,
            date=day,
            description=f"Item {tid}",
            amount_minor=amount_minor,
            currency=currency,
            category=category,
            direction=direction,
        ),
        account_id="acc",
        booked_at=day.toordinal() * 86400,
    )


def sample_db():
    conn = connect(":memory:")
    add(conn, "g", date(2026, 10, 3), 1000, category="Groceries")
    add(conn, "d", date(2026, 10, 4), 500, category="Dining")
    add(conn, "t", date(2026, 10, 5), 9999, category="Transfer")
    add(conn, "i", date(2026, 10, 6), 20000, category="Income", direction=CREDIT)
    add(conn, "p", date(2026, 10, 7), 300, category="Groceries", currency="PLN")
    add(conn, "s", date(2026, 9, 20), 700, category="Groceries")
    return conn


def test_monthly_totals_exclude_transfers_and_never_mix_currencies():
    assert monthly_totals(sample_db()) == [
        MonthlyTotal("2026-09", "UAH", 700, 0),
        MonthlyTotal("2026-10", "PLN", 300, 0),
        MonthlyTotal("2026-10", "UAH", 1500, 20000),
    ]


def test_net_minor():
    assert MonthlyTotal("2026-10", "UAH", 1500, 20000).net_minor == 18500


def test_category_totals_for_a_month_largest_first():
    rows = category_totals(sample_db(), month="2026-10")
    uah = [(r.category, r.spent_minor, r.count) for r in rows if r.currency == "UAH"]
    assert uah == [("Groceries", 1000, 1), ("Dining", 500, 1)]


def test_uncategorized_counts_as_spending():
    conn = connect(":memory:")
    add(conn, "u", date(2026, 10, 3), 250)
    rows = category_totals(conn)
    assert [(r.category, r.spent_minor) for r in rows] == [("Uncategorized", 250)]


def test_partial_months():
    conn = connect(":memory:")
    add(conn, "a", date(2026, 8, 5), 100)
    add(conn, "b", date(2026, 11, 2), 100)
    assert partial_months(conn, date(2026, 11, 21)) == {"2026-08", "2026-11"}

    conn = connect(":memory:")
    add(conn, "a", date(2026, 8, 1), 100)
    add(conn, "b", date(2026, 11, 2), 100)
    assert partial_months(conn, date(2026, 12, 5)) == set()


def test_month_over_month_change_and_percent():
    totals = [
        MonthlyTotal("2026-09", "UAH", 1000, 0),
        MonthlyTotal("2026-10", "UAH", 1500, 0),
        MonthlyTotal("2026-11", "UAH", 900, 0),
    ]
    changes = month_over_month(totals)
    assert [(c.month, c.change_minor, c.change_pct) for c in changes] == [
        ("2026-09", None, None),
        ("2026-10", 500, 50.0),
        ("2026-11", -600, -40.0),
    ]


def test_month_over_month_skips_partial_months_and_other_currencies():
    totals = [
        MonthlyTotal("2026-09", "UAH", 1000, 0),
        MonthlyTotal("2026-10", "UAH", 1500, 0),
        MonthlyTotal("2026-11", "UAH", 900, 0),
        MonthlyTotal("2026-11", "PLN", 100, 0),
    ]
    changes = month_over_month(totals, skip_months={"2026-10"})
    by_key = {(c.month, c.currency): c for c in changes}
    assert ("2026-10", "UAH") not in by_key
    assert by_key[("2026-11", "UAH")].previous_minor is None  # Sep is not adjacent
    assert ("2026-10", "UAH") not in by_key  # skipped month is not reported
    assert by_key[("2026-11", "PLN")].previous_minor is None  # no PLN history


def expense(tid, description, amount_minor, category="Transport"):
    return Transaction(
        transaction_id=tid,
        date=date(2026, 10, 1),
        description=description,
        amount_minor=amount_minor,
        currency="UAH",
        category=category,
    )


def grocery(tid, amount_minor, category="Groceries"):
    return expense(tid, f"Shop {tid}", amount_minor, category)


def test_detect_unusual_flags_only_the_outlier():
    normal = [1000, 1100, 1050, 1200, 1000, 1150, 1100, 1050, 1000]
    items = [grocery(f"n{i}", a) for i, a in enumerate(normal)]
    items.append(grocery("big", 20000))
    flagged = detect_unusual(items)
    assert [u.transaction_id for u in flagged] == ["big"]
    assert flagged[0].typical_minor == 1050
    assert flagged[0].category == "Groceries"
    assert flagged[0].basis == "merchant: shop"


def test_detect_unusual_needs_enough_samples_and_ignores_transfers():
    few = [grocery("a", 1000), grocery("b", 1100), grocery("big", 20000)]
    assert detect_unusual(few) == []

    normal = [1000, 1100, 1050, 1200, 1000, 1150, 1100, 1050, 1000]
    transfers = [grocery(f"n{i}", a, "Transfer") for i, a in enumerate(normal)]
    transfers.append(grocery("big", 20000, "Transfer"))
    assert detect_unusual(transfers) == []


def test_detect_unusual_compares_a_merchant_with_its_own_history():
    cheap = [2000, 2100, 2050, 2000, 2100, 2050]
    pricey = [23000, 23500, 24000, 23000, 23500, 24000]
    items = [expense(f"u{i}", "Uklon", a) for i, a in enumerate(cheap)]
    items += [expense(f"k{i}", "Koleo", a) for i, a in enumerate(pricey)]
    assert detect_unusual(items, min_samples=5) == []


def test_detect_unusual_pools_rare_merchants_by_category():
    items = [
        expense(f"{name}{i}", name, 1000 + i * 50)
        for name in ("A", "B", "C", "D")
        for i in range(2)
    ]
    items.append(expense("big", "Taxi", 20000))
    flagged = detect_unusual(items, min_samples=8)
    assert [(u.transaction_id, u.basis) for u in flagged] == [
        ("big", "category: Transport")
    ]


def test_format_minor_uses_currency_precision():
    assert format_minor(23341, "UAH") == "233.41 UAH"
    assert format_minor(500, "JPY") == "500 JPY"
