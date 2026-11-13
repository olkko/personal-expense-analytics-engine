from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from personal_expense_analytics_engine.enablebanking import (
    EnableBankingClient,
    EnableBankingConfig,
    EnableBankingError,
)
from personal_expense_analytics_engine.enablebanking_mapper import (
    enablebanking_item_to_transaction,
)
from personal_expense_analytics_engine.storage import connect, load_transactions
from personal_expense_analytics_engine.sync import sync_account


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


@pytest.fixture
def client_config(tmp_path: Path):
    key_path = tmp_path / "test-private-key.pem"
    key_path.write_text("test key", encoding="utf-8")
    return EnableBankingConfig(
        app_id="test-app-id",
        private_key_path=key_path,
        redirect_url="https://example.test/callback",
    )


def test_list_aspsps_authenticates_request_and_normalizes_country(client_config):
    session = FakeSession([FakeResponse({"aspsps": [{"name": "Example Bank"}]})])
    client = EnableBankingClient(client_config, session)

    with patch(
        "personal_expense_analytics_engine.enablebanking.jwt.encode",
        return_value="signed-token",
    ) as encode:
        banks = client.list_aspsps("pl")

    assert banks == [{"name": "Example Bank"}]
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", "https://api.enablebanking.com/aspsps")
    assert kwargs["params"] == {"country": "PL"}
    assert kwargs["headers"]["Authorization"] == "Bearer signed-token"
    assert encode.call_args.kwargs["algorithm"] == "RS256"
    assert encode.call_args.kwargs["headers"]["kid"] == "test-app-id"


def test_start_authorization_sends_consent_and_redirect_fields(client_config):
    session = FakeSession([FakeResponse({"url": "https://auth.example.test"})])
    client = EnableBankingClient(client_config, session)
    valid_until = datetime.now(timezone.utc) + timedelta(days=10)

    with patch(
        "personal_expense_analytics_engine.enablebanking.jwt.encode",
        return_value="signed-token",
    ):
        response = client.start_authorization(
            "Example Bank", "pl", "random-state", valid_until
        )

    assert response["url"] == "https://auth.example.test"
    body = session.calls[0][2]["json"]
    assert body["aspsp"] == {"name": "Example Bank", "country": "PL"}
    assert body["state"] == "random-state"
    assert body["redirect_url"] == client_config.redirect_url
    assert body["access"]["valid_until"] == valid_until.isoformat()


def test_authorize_session_exchanges_code_and_returns_accounts(client_config):
    session = FakeSession([FakeResponse({"session_id": "session-1", "accounts": []})])
    client = EnableBankingClient(client_config, session)

    with patch(
        "personal_expense_analytics_engine.enablebanking.jwt.encode",
        return_value="signed-token",
    ):
        response = client.authorize_session("callback-code")

    assert response == {"session_id": "session-1", "accounts": []}
    assert session.calls[0][0:2] == (
        "POST",
        "https://api.enablebanking.com/sessions",
    )
    assert session.calls[0][2]["json"] == {"code": "callback-code"}


def test_get_transactions_follows_continuation_key(client_config):
    session = FakeSession(
        [
            FakeResponse(
                {"transactions": [{"transaction_id": "first"}], "continuation_key": "next"}
            ),
            FakeResponse({"transactions": [{"transaction_id": "second"}]}),
        ]
    )
    client = EnableBankingClient(client_config, session)

    with patch(
        "personal_expense_analytics_engine.enablebanking.jwt.encode",
        return_value="signed-token",
    ):
        items = client.get_transactions(
            "account-uid", date(2026, 1, 1), date(2026, 1, 31)
        )

    assert [item["transaction_id"] for item in items] == ["first", "second"]
    assert session.calls[0][2]["params"] == {
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
    }
    assert session.calls[1][2]["params"]["continuation_key"] == "next"


def test_api_errors_are_reported_without_response_body(client_config):
    client = EnableBankingClient(
        client_config, FakeSession([FakeResponse({"error": "details"}, status_code=401)])
    )

    with patch(
        "personal_expense_analytics_engine.enablebanking.jwt.encode",
        return_value="signed-token",
    ):
        with pytest.raises(EnableBankingError, match="HTTP 401"):
            client.list_aspsps("PL")


def test_request_jwt_is_signed_with_registered_application_key(tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_path = tmp_path / "private-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,  # type: ignore[arg-type]
            format=serialization.PrivateFormat.PKCS8,  # type: ignore[arg-type]
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    session = FakeSession([FakeResponse({"aspsps": []})])
    client = EnableBankingClient(
        EnableBankingConfig(
            app_id="test-app-id",
            private_key_path=private_key_path,
            redirect_url="https://example.test/callback",
        ),
        session,
    )

    client.list_aspsps("PL")
    token = session.calls[0][2]["headers"]["Authorization"].removeprefix(
        "Bearer "
    )
    claims = jwt.decode(
        token,
        private_key.public_key(),
        algorithms=["RS256"],
        audience="api.enablebanking.com",
        issuer="enablebanking.com",
    )

    assert jwt.get_unverified_header(token)["kid"] == "test-app-id"
    assert claims["exp"] - claims["iat"] == 3600


def test_mapper_uses_currency_precision_and_account_scoped_identity():
    item = {
        "entry_reference": "same-reference",
        "transaction_amount": {"amount": "123", "currency": "JPY"},
        "credit_debit_indicator": "DBIT",
        "status": "BOOK",
        "booking_date": "2026-10-30",
        "creditor": {"name": "Market"},
        "remittance_information": ["  Groceries  "],
    }

    first = enablebanking_item_to_transaction(item, "account-one")
    second = enablebanking_item_to_transaction(item, "account-two")

    assert first.amount_minor == 123
    assert first.amount == 123
    assert first.currency == "JPY"
    assert first.direction.value == "debit"
    assert first.description == "Groceries"
    assert first.merchant == "Market"
    assert first.transaction_id != second.transaction_id


def test_mapper_rejects_unknown_credit_debit_indicator():
    item = {
        "transaction_amount": {"amount": "1.00", "currency": "EUR"},
        "credit_debit_indicator": "UNKNOWN",
        "booking_date": "2026-10-30",
        "remittance_information": ["Payment"],
    }

    with pytest.raises(ValueError, match="indicator"):
        enablebanking_item_to_transaction(item, "account-one")


def test_sync_persists_transactions_from_enable_banking():
    conn = connect(":memory:")
    item = {
        "entry_reference": "bank-ref",
        "transaction_amount": {"amount": "12.34", "currency": "EUR"},
        "credit_debit_indicator": "DBIT",
        "status": "PDNG",
        "booking_date": "2033-05-17",
        "merchant_category_code": "5411",
        "creditor": {"name": "Shop"},
        "remittance_information": ["Groceries"],
    }

    class TransactionClient:
        def get_transactions(self, account_uid, date_from, date_to):
            assert account_uid == "account-one"
            return [item]

    count = sync_account(
        conn,
        TransactionClient(),
        "account-one",
        initial_days=30,
        now=int(datetime(2033, 5, 17, tzinfo=timezone.utc).timestamp()),
    )

    transactions = load_transactions(conn, "account-one")
    assert count == 1
    assert len(transactions) == 1
    assert transactions[0].amount_minor == 1234
    row = conn.execute(
        "SELECT is_pending, mcc FROM transactions WHERE account_id = ?",
        ("account-one",),
    ).fetchone()
    assert row == (1, 5411)
