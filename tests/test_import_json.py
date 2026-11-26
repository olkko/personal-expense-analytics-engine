import json
from datetime import date

import pytest

from personal_expense_analytics_engine._import_json import (
    import_export,
    load_export,
    main,
)
from personal_expense_analytics_engine._storage import connect, load_transactions

ACCOUNT = "7b2e4c1a-9d3f-4a61-b5c8-2f0e91d6a7c4"


def record(
    ref: str,
    day: str,
    amount: str,
    indicator: str = "DBIT",
    name: str = "BIEDRONKA",
    mcc: str | None = "5411",
    text: str | None = None,
):
    party = {"name": name}
    return {
        "entry_reference": ref,
        "merchant_category_code": mcc,
        "transaction_amount": {"currency": "PLN", "amount": amount},
        "creditor": party if indicator == "DBIT" else None,
        "debtor": party if indicator == "CRDT" else None,
        "credit_debit_indicator": indicator,
        "status": "BOOK",
        "booking_date": day,
        "value_date": day,
        "remittance_information": [text or name],
    }


@pytest.fixture
def export_file(tmp_path):
    data = {
        "accounts": [
            {
                "info": {"resource_id": ACCOUNT, "currency": "PLN"},
                "transactions": [
                    record("1", "2026-10-01", "45.20"),
                    record(
                        "2",
                        "2026-10-02",
                        "600.0",
                        name="Konto Oszczędnościowe",
                        mcc=None,
                        text="Przelew na konto oszczędnościowe",
                    ),
                    record(
                        "3",
                        "2026-10-10",
                        "8500.0",
                        indicator="CRDT",
                        name="ACME Sp. z o.o.",
                        mcc=None,
                        text="Wynagrodzenie",
                    ),
                ],
                "balances": [],
            }
        ]
    }
    path = tmp_path / "export.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_export_reads_accounts(export_file):
    accounts = load_export(export_file)
    assert [a.account_id for a in accounts] == [ACCOUNT]
    assert len(accounts[0].transactions) == 3


def test_import_stores_and_categorizes_idempotently(tmp_path, export_file):
    conn = connect(tmp_path / "db.sqlite")
    try:
        first = import_export(conn, export_file)
        import_export(conn, export_file)  # re-import must not duplicate rows
        rows = {t.description: t for t in load_transactions(conn)}
    finally:
        conn.close()

    assert first.accounts == {ACCOUNT: 3}
    assert len(rows) == 3
    assert rows["BIEDRONKA"].amount_minor == 4520
    assert rows["BIEDRONKA"].date == date(2026, 10, 1)
    assert rows["BIEDRONKA"].category == "Groceries"
    assert rows["Przelew na konto oszczędnościowe"].category == "Transfer"
    assert rows["Wynagrodzenie"].category == "Income"


def test_import_keeps_manual_categories(tmp_path, export_file):
    conn = connect(tmp_path / "db.sqlite")
    try:
        import_export(conn, export_file)
        conn.execute(
            "UPDATE transactions SET category = 'Dining' WHERE amount_minor = 4520"
        )
        conn.commit()
        import_export(conn, export_file)
        category = conn.execute(
            "SELECT category FROM transactions WHERE amount_minor = 4520"
        ).fetchone()[0]
    finally:
        conn.close()
    assert category == "Dining"


def test_rejects_file_without_accounts(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="accounts"):
        load_export(path)


def test_cli_prints_summary(tmp_path, export_file, capsys):
    database = tmp_path / "cli.sqlite"
    main([str(export_file), "--database", str(database)])
    out = capsys.readouterr().out
    assert f"Imported 3 transactions for account {ACCOUNT}" in out
    assert "- Groceries: 1" in out
    assert database.is_file()
