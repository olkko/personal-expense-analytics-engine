from contextlib import closing
from datetime import date
from pathlib import Path

from streamlit.testing.v1 import AppTest

from personal_expense_analytics_engine._models import Transaction, TransactionDirection
from personal_expense_analytics_engine._storage import (
    connect,
    load_transactions,
    upsert_transaction,
)


def test_dashboard_saves_category_and_refreshes_transaction_table(
    tmp_path: Path,
    monkeypatch,
):
    database = tmp_path / "expenses.db"
    with closing(connect(database)) as conn:
        upsert_transaction(
            conn,
            Transaction(
                "sample-transaction",
                date(2026, 10, 4),
                "Sample electricity",
                7_900,
                "PLN",
            ),
            account_id="sample-account",
            booked_at=3,
        )
        upsert_transaction(
            conn,
            Transaction(
                "sample-transfer",
                date(2026, 10, 5),
                "Sample transfer",
                5_000,
                "PLN",
                category="Transfer",
            ),
            account_id="sample-account",
            booked_at=2,
        )
        upsert_transaction(
            conn,
            Transaction(
                "sample-income",
                date(2026, 10, 6),
                "Sample salary",
                100_000,
                "PLN",
                category="Income",
                direction=TransactionDirection.CREDIT,
            ),
            account_id="sample-account",
            booked_at=1,
        )
        conn.commit()

    monkeypatch.setenv("EXPENSES_DB", str(database))
    dashboard = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "personal_expense_analytics_engine"
        / "_dashboard.py"
    )
    app = AppTest.from_file(dashboard).run()
    assert not app.exception
    captions = [item.value for item in app.caption]
    assert captions.count("1 transaction") == 2

    editor = app.data_editor[0]
    assert editor.key is not None
    app.session_state[editor.key] = {
        "edited_rows": {0: {"category": "Utilities"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    app.form_submit_button[0].click().run()

    assert not app.exception
    assert app.data_editor[0].value.loc[0, "category"] == "Utilities"
    with closing(connect(database)) as conn:
        transactions = load_transactions(conn, "sample-account")
    assert transactions[0].category == "Utilities"
