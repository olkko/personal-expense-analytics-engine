"""Client for Enable Banking's Account Information API."""

import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import jwt
import requests
from dotenv import load_dotenv

API_ORIGIN = "https://api.enablebanking.com"


class _HttpSession(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int,
    ) -> Any:
        """Send an HTTP request through the underlying session.

        Args:
            method (str): HTTP method.
            url (str): Request URL.
            headers (dict[str, str]): Request headers.
            params (dict[str, str] | None): Optional query parameters.
            json (dict[str, Any] | None): Optional JSON request body.
            timeout (int): Request timeout in seconds.

        Returns:
            Any: HTTP response object.
        """
        ...


class EnableBankingError(RuntimeError):
    """Raised when Enable Banking configuration or an API request is invalid."""


@dataclass(frozen=True, slots=True)
class EnableBankingConfig:
    """Credentials and callback configuration for an Enable Banking application."""

    app_id: str
    private_key_path: Path
    redirect_url: str
    api_origin: str = API_ORIGIN

    @classmethod
    def from_environment(cls) -> "EnableBankingConfig":
        """Load configuration from environment variables and an optional .env file.

        Returns:
            EnableBankingConfig: The validated application configuration.

        Raises:
            EnableBankingError: If a required environment variable is missing.
        """
        load_dotenv()
        required = (
            "ENABLE_BANKING_APP_ID",
            "ENABLE_BANKING_PRIVATE_KEY_PATH",
            "ENABLE_BANKING_REDIRECT_URL",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise EnableBankingError(
                "Missing Enable Banking configuration: " + ", ".join(missing)
            )
        return cls(
            app_id=os.environ["ENABLE_BANKING_APP_ID"],
            private_key_path=Path(os.environ["ENABLE_BANKING_PRIVATE_KEY_PATH"]),
            redirect_url=os.environ["ENABLE_BANKING_REDIRECT_URL"],
            api_origin=os.environ.get("ENABLE_BANKING_API_ORIGIN", API_ORIGIN),
        )

    def __post_init__(self) -> None:
        """Reject incomplete application configuration.

        Raises:
            ValueError: If required values are empty or the API origin is not HTTPS.
        """
        if not self.app_id.strip():
            raise ValueError("Enable Banking app_id cannot be empty.")
        if not self.redirect_url.strip():
            raise ValueError("Enable Banking redirect_url cannot be empty.")
        if not self.api_origin.startswith("https://"):
            raise ValueError("Enable Banking API origin must use HTTPS.")
        object.__setattr__(self, "api_origin", self.api_origin.rstrip("/"))


class EnableBankingClient:
    """Sign requests and call Enable Banking account-information endpoints."""

    def __init__(
        self,
        config: EnableBankingConfig,
        session: _HttpSession | None = None,
    ) -> None:
        """Create a client and load its private signing key.

        Args:
            config (EnableBankingConfig): Application credentials and callback settings.
            session (_HttpSession | None): Optional HTTP session, mainly for testing.

        Raises:
            OSError: If the private key file cannot be read.
        """
        self.config = config
        self._private_key = config.private_key_path.read_bytes()
        self._session: _HttpSession = session if session is not None else requests.Session()

    @classmethod
    def from_environment(cls) -> "EnableBankingClient":
        """Create a client from ENABLE_BANKING_* environment variables.

        Returns:
            EnableBankingClient: A configured API client.

        Raises:
            EnableBankingError: If required environment variables are missing.
            OSError: If the private key file cannot be read.
        """
        return cls(EnableBankingConfig.from_environment())

    def list_aspsps(self, country: str) -> list[dict[str, Any]]:
        """Return banks supported in the requested two-letter country.

        Args:
            country (str): Two-letter ISO country code.

        Returns:
            list[dict[str, Any]]: Supported banks returned by the API.

        Raises:
            ValueError: If country is not a two-letter alphabetic code.
            EnableBankingError: If the API request or response is invalid.
        """
        country_code = country.strip().upper()
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("Country must be a two-letter ISO country code.")
        response = self._request("GET", "/aspsps", params={"country": country_code})
        aspsps = response.get("aspsps")
        if not isinstance(aspsps, list):
            raise EnableBankingError("Enable Banking response omitted the ASPSP list.")
        return aspsps

    def start_authorization(
        self,
        bank_name: str,
        country: str,
        state: str,
        valid_until: datetime,
    ) -> dict[str, Any]:
        """Start bank consent and return the redirect URL and authorization ID.

        The caller must retain and verify `state` when the bank redirects back.

        Args:
            bank_name (str): Enable Banking ASPSP name.
            country (str): Two-letter ISO country code for the ASPSP.
            state (str): Caller-generated OAuth state to verify on callback.
            valid_until (datetime): Timezone-aware consent expiration time.

        Returns:
            dict[str, Any]: Authorization details, including the redirect URL.

        Raises:
            ValueError: If an input is empty, invalid, or timezone-naive.
            EnableBankingError: If the API request or response is invalid.
        """
        if not bank_name.strip():
            raise ValueError("Bank name cannot be empty.")
        if not state.strip():
            raise ValueError("OAuth state cannot be empty.")
        if valid_until.tzinfo is None or valid_until.utcoffset() is None:
            raise ValueError("Consent expiry must be timezone-aware.")
        country_code = country.strip().upper()
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("Country must be a two-letter ISO country code.")

        return self._request(
            "POST",
            "/auth",
            json={
                "access": {
                    "transactions": True,
                    "balances": True,
                    "valid_until": valid_until.isoformat(),
                },
                "aspsp": {"name": bank_name, "country": country_code},
                "state": state,
                "redirect_url": self.config.redirect_url,
                "psu_type": "personal",
            },
        )

    def authorize_session(self, code: str) -> dict[str, Any]:
        """Exchange the callback authorization code for a bank session.

        Args:
            code (str): Authorization code received by the registered callback.

        Returns:
            dict[str, Any]: Created session details, including linked accounts.

        Raises:
            ValueError: If the authorization code is empty.
            EnableBankingError: If the API request or session response is invalid.
        """
        if not code.strip():
            raise ValueError("Authorization code cannot be empty.")
        session = self._request("POST", "/sessions", json={"code": code})
        if not isinstance(session.get("accounts"), list):
            raise EnableBankingError("Enable Banking session response omitted accounts.")
        return session

    def get_transactions(
        self,
        account_uid: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, Any]]:
        """Fetch all transaction pages for an account over an inclusive date range.

        Args:
            account_uid (str): Enable Banking account identifier.
            date_from (date): First transaction date to include.
            date_to (date): Last transaction date to include.

        Returns:
            list[dict[str, Any]]: Transaction records from all result pages.

        Raises:
            ValueError: If the account UID is empty or the date range is reversed.
            EnableBankingError: If the API returns an invalid page or continuation key.
        """
        if not account_uid.strip():
            raise ValueError("Account UID cannot be empty.")
        if date_from > date_to:
            raise ValueError("date_from must not be after date_to.")

        path = f"/accounts/{quote(account_uid, safe='')}/transactions"
        base_params = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        }
        transactions: list[dict[str, Any]] = []
        seen_continuation_keys: set[str] = set()
        continuation_key: str | None = None

        while True:
            params = dict(base_params)
            if continuation_key:
                params["continuation_key"] = continuation_key
            response = self._request("GET", path, params=params)
            page = response.get("transactions")
            if not isinstance(page, list):
                raise EnableBankingError(
                    "Enable Banking response omitted the transaction list."
                )
            transactions.extend(page)

            next_continuation_key = response.get("continuation_key")
            if not next_continuation_key:
                return transactions
            if not isinstance(next_continuation_key, str):
                raise EnableBankingError(
                    "Enable Banking returned an invalid transaction continuation key."
                )
            if next_continuation_key in seen_continuation_keys:
                raise EnableBankingError(
                    "Enable Banking repeated a transaction continuation key."
                )
            seen_continuation_keys.add(next_continuation_key)
            continuation_key = next_continuation_key

    def close_session(self, session_id: str) -> None:
        """Close a consent session and ask the bank to revoke access when possible.

        Args:
            session_id (str): Enable Banking session identifier.

        Raises:
            ValueError: If the session ID is empty.
            EnableBankingError: If the API request fails.
        """
        if not session_id.strip():
            raise ValueError("Session ID cannot be empty.")
        self._request("DELETE", f"/sessions/{quote(session_id, safe='')}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request and return its JSON object response.

        Args:
            method (str): HTTP method.
            path (str): API endpoint path.
            params (dict[str, str] | None): Optional query parameters.
            json (dict[str, Any] | None): Optional JSON request body.

        Returns:
            dict[str, Any]: JSON object returned by the API.

        Raises:
            EnableBankingError: If the request fails or the response is invalid.
        """
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": "enablebanking.com",
                "aud": "api.enablebanking.com",
                "iat": now,
                "exp": now + 3600,
            },
            self._private_key,
            algorithm="RS256",
            headers={"kid": self.config.app_id, "typ": "JWT"},
        )
        try:
            response = self._session.request(
                method,
                f"{self.config.api_origin}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=json,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise EnableBankingError("Enable Banking request failed.") from exc

        if not response.ok:
            raise EnableBankingError(
                f"Enable Banking returned HTTP {response.status_code}."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise EnableBankingError("Enable Banking returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise EnableBankingError("Enable Banking response must be a JSON object.")
        return payload
