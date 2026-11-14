import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from personal_expense_analytics_engine.enablebanking import (
    EnableBankingConfig,
    EnableBankingError,
)
from personal_expense_analytics_engine.enablebanking_cli import (
    AuthorizationCallbackError,
    _extract_authorization_code,
    _local_callback_target,
    _select_account_uid,
    _wait_for_authorization_code,
    authorize_bank,
    main,
)
from personal_expense_analytics_engine.storage import connect, load_transactions


class FakeAuthorizationClient:
    def __init__(self, redirect_url: str, authorization_url: str):
        self.config = EnableBankingConfig(
            app_id="test-app",
            private_key_path=Path("unused-test-key.pem"),
            redirect_url=redirect_url,
        )
        self.authorization_url = authorization_url
        self.authorization_args: tuple[str, str, str, datetime] | None = None
        self.authorized_code: str | None = None
        self.transaction_calls: list[tuple[str, date, date]] = []
        self.transaction_items: list[dict[str, Any]] = []

    def start_authorization(
        self,
        bank_name: str,
        country: str,
        state: str,
        valid_until: datetime,
    ) -> dict[str, Any]:
        self.authorization_args = (bank_name, country, state, valid_until)
        return {"url": self.authorization_url}

    def authorize_session(self, code: str) -> dict[str, Any]:
        self.authorized_code = code
        return {"session_id": "session-1", "accounts": []}

    def get_transactions(
        self,
        account_uid: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        self.transaction_calls.append((account_uid, date_from, date_to))
        return self.transaction_items


def test_local_callback_target_accepts_loopback_http_url():
    assert _local_callback_target("http://localhost:8765/callback") == (
        "127.0.0.1",
        8765,
        "/callback",
    )


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://example.com/callback",
        "http://example.com/callback",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:8765/callback?next=/",
    ],
)
def test_local_callback_target_rejects_unsupported_urls(redirect_url):
    with pytest.raises(ValueError, match="registered redirect URL"):
        _local_callback_target(redirect_url)


def test_callback_returns_code_only_when_state_matches():
    code = _extract_authorization_code(
        "/callback?code=one-time-code&state=expected-state",
        "/callback",
        "expected-state",
    )

    assert code == "one-time-code"


def test_callback_rejects_mismatched_state():
    with pytest.raises(ValueError, match="state"):
        _extract_authorization_code(
            "/callback?code=one-time-code&state=wrong-state",
            "/callback",
            "expected-state",
        )


def test_callback_reports_provider_authorization_error():
    with pytest.raises(AuthorizationCallbackError, match="did not complete"):
        _extract_authorization_code(
            "/callback?error=access_denied&state=expected-state",
            "/callback",
            "expected-state",
        )


def test_local_server_rejects_bad_state_then_accepts_valid_callback(monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    redirect_url = f"http://127.0.0.1:{port}/callback"
    expected_state = "expected-state"
    callback_futures = []

    def send_callbacks() -> tuple[int, int]:
        invalid_callback = (
            f"{redirect_url}?code=attacker-code&state=wrong-state"
        )
        try:
            urlopen(invalid_callback, timeout=3)
        except HTTPError as exc:
            invalid_status = exc.code
        else:
            raise AssertionError("A callback with the wrong state was accepted.")

        valid_callback = (
            f"{redirect_url}?code=verified-code&state={expected_state}"
        )
        with urlopen(valid_callback, timeout=3) as response:
            return invalid_status, response.status

    with ThreadPoolExecutor(max_workers=1) as executor:
        def fake_browser_open(url: str, new: int = 0) -> bool:
            callback_futures.append(executor.submit(send_callbacks))
            return True

        monkeypatch.setattr(
            "personal_expense_analytics_engine.enablebanking_cli.webbrowser.open",
            fake_browser_open,
        )
        code = _wait_for_authorization_code(
            redirect_url,
            expected_state,
            "https://auth.example.test",
            timeout_seconds=5,
        )
        invalid_status, valid_status = callback_futures[0].result(timeout=3)

    assert invalid_status == 400
    assert valid_status == 200
    assert code == "verified-code"


def test_authorize_bank_exchanges_only_the_verified_callback_code():
    client = FakeAuthorizationClient(
        "http://127.0.0.1:8765/callback",
        "https://auth.example.test",
    )

    with patch(
        "personal_expense_analytics_engine.enablebanking_cli._wait_for_authorization_code",
        return_value="verified-code",
    ) as wait_for_code:
        session = authorize_bank(client, "Mock ASPSP", "PL")

    assert client.authorization_args is not None
    _, _, state, valid_until = client.authorization_args
    assert len(state) >= 32
    assert valid_until.utcoffset() == timedelta(0)
    wait_for_code.assert_called_once_with(
        "http://127.0.0.1:8765/callback",
        state,
        "https://auth.example.test",
        300,
    )
    assert client.authorized_code == "verified-code"
    assert session["accounts"] == []


def test_authorize_bank_rejects_non_https_provider_url():
    client = FakeAuthorizationClient(
        "http://127.0.0.1:8765/callback",
        "http://auth.example.test",
    )

    with pytest.raises(EnableBankingError, match="invalid authorization URL"):
        authorize_bank(client, "Mock ASPSP", "PL")


def test_select_account_uid_prompts_when_multiple_accounts_exist(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "2")

    account_uid = _select_account_uid(
        [
            {"uid": "first-account", "currency": "DKK"},
            {"uid": "second-account", "currency": "EUR"},
        ]
    )

    assert account_uid == "second-account"
    output = capsys.readouterr().out
    assert "DKK" in output
    assert "EUR" in output
    assert "second-account" not in output


def test_main_fetches_maps_and_persists_selected_account_transactions(
    tmp_path: Path,
    capsys,
):
    client = FakeAuthorizationClient(
        "http://127.0.0.1:8765/callback",
        "https://auth.example.test",
    )
    client.transaction_items = [
        {
            "transaction_amount": {"amount": "88.0", "currency": "DKK"},
            "credit_debit_indicator": "DBIT",
            "status": "BOOK",
            "booking_date": "2020-09-14",
            "creditor": {"name": "Demo shop"},
            "remittance_information": ["Synthetic purchase"],
            "merchant_category_code": "5411",
        }
    ]
    database_path = tmp_path / "expenses.db"
    session = {"accounts": [{"uid": "demo-account", "currency": "DKK"}]}

    with (
        patch(
            "personal_expense_analytics_engine.enablebanking_cli.EnableBankingClient.from_environment",
            return_value=client,
        ),
        patch(
            "personal_expense_analytics_engine.enablebanking_cli.authorize_bank",
            return_value=session,
        ),
    ):
        main(
            [
                "--bank",
                "Mock ASPSP",
                "--country",
                "PL",
                "--date-from",
                "2020-09-01",
                "--date-to",
                "2020-09-30",
                "--database",
                str(database_path),
            ]
        )

    assert client.transaction_calls == [
        ("demo-account", date(2020, 9, 1), date(2020, 9, 30))
    ]
    with connect(database_path) as conn:
        transactions = load_transactions(conn, "demo-account")
    assert len(transactions) == 1
    assert transactions[0].description == "Synthetic purchase"
    assert transactions[0].amount_minor == 8800
    assert "Fetched 1 transaction(s)" in capsys.readouterr().out
