from datetime import date

from personal_expense_analytics_engine.categorization import categorize, category_type
from personal_expense_analytics_engine.duplicates import find_probable_duplicates
from personal_expense_analytics_engine.models import (
    CategoryType,
    Transaction,
    TransactionDirection,
)
from personal_expense_analytics_engine.normalization import merchant_key, normalize_text
from personal_expense_analytics_engine.processing import categorize_stored
from personal_expense_analytics_engine.recurring import detect_recurring
from personal_expense_analytics_engine.storage import connect, load_transactions, upsert_transaction

DEBIT = TransactionDirection.DEBIT
CREDIT = TransactionDirection.CREDIT


def txn(day, description, amount_minor, direction=DEBIT, category=None, txn_id=None):
    return Transaction(
        transaction_id=txn_id or f"{description}-{day.isoformat()}",
        date=day,
        description=description,
        amount_minor=amount_minor,
        currency="UAH",
        category=category,
        direction=direction,
    )


# --- normalization ---

def test_normalize_text_removes_noise_and_case():
    assert normalize_text("  KOLEO   BILETY #123 ") == "koleo bilety"


def test_merchant_key_uses_first_word():
    assert merchant_key("Spotify P3A9") == "spotify"
    assert merchant_key("") == ""


# --- categorization ---

def test_own_card_transfer():
    assert categorize("З Білої картки", direction=CREDIT) == "Transfer"
    assert categorize("На чорну картку", direction=DEBIT) == "Transfer"


def test_incoming_from_person_is_income():
    assert categorize("Від: Оксана", direction=CREDIT) == "Income"


def test_keyword_rules():
    assert categorize("Холодна вода", direction=DEBIT) == "Utilities"
    assert categorize("KOLEO BILETY KOLEJOWE", direction=DEBIT, mcc=4789) == "Transport"


def test_mcc_rule_and_fallbacks():
    assert categorize("Random Shop", direction=DEBIT, mcc=5411) == "Groceries"
    assert categorize("Random Shop", direction=DEBIT) is None
    assert categorize("Salary ACME", direction=CREDIT) == "Income"


def test_category_types():
    assert category_type("Transfer") is CategoryType.TRANSFER
    assert category_type("Subscriptions") is CategoryType.FIXED
    assert category_type("Groceries") is CategoryType.VARIABLE


# --- recurring ---

def test_monthly_subscription_is_detected():
    items = [
        txn(date(2026, 8, 5), "Spotify", 5999),
        txn(date(2026, 9, 4), "Spotify", 5999),
        txn(date(2026, 10, 5), "Spotify", 5999),
    ]
    found = detect_recurring(items)
    assert len(found) == 1
    assert found[0].key == "spotify"
    assert found[0].frequency == "monthly"
    assert found[0].typical_amount_minor == 5999
    assert found[0].occurrences == 3
    assert found[0].next_expected_date == date(2026, 11, 4)


def test_varying_amounts_are_not_recurring():
    items = [
        txn(date(2026, 8, 5), "Silpo", 1000),
        txn(date(2026, 9, 4), "Silpo", 5000),
        txn(date(2026, 10, 5), "Silpo", 2000),
    ]
    assert detect_recurring(items) == []


def test_irregular_dates_are_not_recurring():
    items = [
        txn(date(2026, 8, 5), "Gym", 3000),
        txn(date(2026, 8, 9), "Gym", 3000),
        txn(date(2026, 10, 30), "Gym", 3000),
    ]
    assert detect_recurring(items) == []


def test_transfers_and_too_few_occurrences_are_ignored():
    transfers = [
        txn(date(2026, 8, 5), "Rent", 100000, category="Transfer"),
        txn(date(2026, 9, 4), "Rent", 100000, category="Transfer"),
        txn(date(2026, 10, 5), "Rent", 100000, category="Transfer"),
    ]
    assert detect_recurring(transfers) == []
    two = [txn(date(2026, 9, 4), "Spotify", 5999), txn(date(2026, 10, 5), "Spotify", 5999)]
    assert detect_recurring(two) == []


# --- duplicates ---

def add(conn, tid, booked_at, description="Coffee", amount_minor=500):
    upsert_transaction(
        conn,
        txn(date(2026, 10, 1), description, amount_minor, txn_id=tid),
        account_id="acc",
        booked_at=booked_at,
    )


def test_probable_duplicates_must_be_close_in_time_and_identical():
    conn = connect(":memory:")
    add(conn, "a", 1000)
    add(conn, "b", 1030)
    add(conn, "c", 1000 + 3600)            # same data, an hour later
    add(conn, "d", 1010, amount_minor=900)  # different amount
    assert find_probable_duplicates(conn, window_seconds=120) == [["a", "b"]]


# --- processing on stored data ---

def test_categorize_stored_fills_only_uncategorized():
    conn = connect(":memory:")
    upsert_transaction(conn, txn(date(2026, 10, 1), "Random Shop", 1000, txn_id="a"),
                       account_id="acc", booked_at=1000, mcc=5411)
    upsert_transaction(conn, txn(date(2026, 10, 2), "Random Shop", 1000, category="Custom", txn_id="b"),
                       account_id="acc", booked_at=2000, mcc=5411)
    upsert_transaction(conn, txn(date(2026, 10, 3), "Mystery", 1000, txn_id="c"),
                       account_id="acc", booked_at=3000)

    counts = categorize_stored(conn)

    by_id = {t.transaction_id: t.category for t in load_transactions(conn)}
    assert by_id == {"a": "Groceries", "b": "Custom", "c": None}
    assert counts == {"Groceries": 1, "Uncategorized": 1}


def test_merchant_key_uses_name_after_payment_processor_star():
    assert merchant_key("LIQPAY*Albion") == "albion"
    assert merchant_key("GOOGLE *YouTube Premium") == "youtube"
    assert merchant_key("Spotify*") == "spotify"


def test_rules_added_after_first_real_run():
    assert categorize("MEGOGO", direction=DEBIT, mcc=7841) == "Subscriptions"
    assert categorize("Нова пошта", direction=DEBIT, mcc=8999) == "Delivery"
    assert categorize("Temu", direction=DEBIT, mcc=5262) == "Shopping"
    assert categorize("ZooBonus", direction=DEBIT, mcc=5995) == "Pets"
    assert categorize("LIQPAY*Albion", direction=DEBIT, mcc=4816) == "Subscriptions"
