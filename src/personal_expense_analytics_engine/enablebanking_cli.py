"""Local command-line flow for Enable Banking authorization."""

import argparse
import secrets
import time
import webbrowser
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from personal_expense_analytics_engine.enablebanking import (
    EnableBankingClient,
    EnableBankingConfig,
    EnableBankingError,
)
from personal_expense_analytics_engine.storage import connect, load_transactions
from personal_expense_analytics_engine.sync import sync_account_range

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


class AuthorizationCallbackError(RuntimeError):
    """Raised when the bank denies authorization or returns an OAuth error."""


class _InvalidCallback(ValueError):
    """Raised when a callback request is malformed or has an invalid state."""


class _AuthorizationClient(Protocol):
    config: EnableBankingConfig

    def start_authorization(
        self,
        bank_name: str,
        country: str,
        state: str,
        valid_until: datetime,
    ) -> dict[str, Any]:
        """Start the bank consent flow and return its redirect URL."""
        ...

    def authorize_session(self, code: str) -> dict[str, Any]:
        """Exchange an authorization code for a session."""
        ...


def _local_callback_target(redirect_url: str) -> tuple[str, int, str]:
    """Validate a loopback HTTP redirect URL and return its bind address.

    Args:
        redirect_url (str): Redirect URL registered for the application.

    Returns:
        tuple[str, int, str]: Local bind host, port, and callback path.

    Raises:
        ValueError: If the redirect URL is not a supported loopback HTTP URL.
    """
    parsed = urlsplit(redirect_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOCAL_HOSTS
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "The local authorization command requires a registered redirect URL "
            "such as http://127.0.0.1:8765/callback."
        )
    return "127.0.0.1", parsed.port, parsed.path or "/"


def _extract_authorization_code(
    request_target: str,
    callback_path: str,
    expected_state: str,
) -> str:
    """Validate a callback request and extract its authorization code.

    Args:
        request_target (str): HTTP request path and query string.
        callback_path (str): Exact callback path expected from the provider.
        expected_state (str): Random state generated for this authorization.

    Returns:
        str: Authorization code from a valid callback.

    Raises:
        _InvalidCallback: If the path, state, or code is missing or invalid.
        AuthorizationCallbackError: If the provider returned an OAuth error.
    """
    parsed = urlsplit(request_target)
    if parsed.path != callback_path:
        raise _InvalidCallback("Callback path did not match.")

    query = parse_qs(parsed.query, keep_blank_values=True)
    states = query.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], expected_state):
        raise _InvalidCallback("Callback state did not match.")

    if query.get("error"):
        raise AuthorizationCallbackError("The bank did not complete authorization.")

    codes = query.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise _InvalidCallback("Callback did not contain one authorization code.")
    return codes[0]


def _wait_for_authorization_code(
    redirect_url: str,
    expected_state: str,
    authorization_url: str,
    timeout_seconds: int,
) -> str:
    """Open the bank authorization page and wait for a verified local callback.

    Args:
        redirect_url (str): Registered loopback callback URL.
        expected_state (str): Random state to validate on return.
        authorization_url (str): Enable Banking URL to open in the browser.
        timeout_seconds (int): Maximum time to wait for the callback.

    Returns:
        str: Verified authorization code.

    Raises:
        AuthorizationCallbackError: If the bank denies or times out authorization.
        ValueError: If the callback URL is unsupported.
    """
    bind_host, port, callback_path = _local_callback_target(redirect_url)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            """Validate a callback request and return a minimal browser response."""
            try:
                code = _extract_authorization_code(
                    self.path,
                    callback_path,
                    expected_state,
                )
            except _InvalidCallback:
                self._respond(
                    400,
                    "This authorization callback was not accepted. You may retry.",
                )
                return
            except AuthorizationCallbackError as exc:
                result["error"] = str(exc)
                self._respond(400, "Authorization was not completed.")
                return

            result["code"] = code
            self._respond(200, "Authorization complete. You may close this tab.")

        def log_message(self, format: str, *args: object) -> None:
            """Suppress access logs that could include the one-time authorization code."""

        def _respond(self, status: int, message: str) -> None:
            """Send a non-cacheable response without echoing callback parameters."""
            body = (
                "<!doctype html><html><body><p>"
                + message
                + "</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    if timeout_seconds < 1:
        raise ValueError("timeout_seconds must be at least one.")

    deadline = time.monotonic() + timeout_seconds
    with HTTPServer((bind_host, port), CallbackHandler) as server:
        if not webbrowser.open(authorization_url, new=1):
            print(f"Open this URL to continue authorization:\n{authorization_url}")
        print(f"Waiting up to {timeout_seconds} seconds for the bank callback...")

        while not result:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthorizationCallbackError(
                    "Timed out waiting for the bank authorization callback."
                )
            server.timeout = min(1.0, remaining)
            server.handle_request()

    if "error" in result:
        raise AuthorizationCallbackError(result["error"])
    return result["code"]


def authorize_bank(
    client: _AuthorizationClient,
    bank_name: str,
    country: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run the local OAuth flow and exchange its verified code for a session.

    Args:
        client (EnableBankingClient): Configured Enable Banking API client.
        bank_name (str): ASPSP name returned by the bank discovery endpoint.
        country (str): Two-letter ISO country code for the ASPSP.
        timeout_seconds (int): Maximum time to wait for the browser callback.

    Returns:
        dict[str, Any]: Session response containing the linked accounts.

    Raises:
        AuthorizationCallbackError: If authorization fails or times out.
        EnableBankingError: If an Enable Banking API request fails.
        ValueError: If the callback URL is not a supported loopback URL.
    """
    _local_callback_target(client.config.redirect_url)
    state = secrets.token_urlsafe(32)
    authorization = client.start_authorization(
        bank_name,
        country,
        state,
        datetime.now(timezone.utc) + timedelta(days=1),
    )
    authorization_url = authorization.get("url")
    if (
        not isinstance(authorization_url, str)
        or urlsplit(authorization_url).scheme != "https"
        or not urlsplit(authorization_url).netloc
    ):
        raise EnableBankingError(
            "Enable Banking returned an invalid authorization URL."
        )

    code = _wait_for_authorization_code(
        client.config.redirect_url,
        state,
        authorization_url,
        timeout_seconds,
    )
    return client.authorize_session(code)


def _parse_iso_date(value: str) -> date:
    """Parse an ISO calendar date for command-line input.

    Args:
        value (str): Date string in ``YYYY-MM-DD`` format.

    Returns:
        date: Parsed calendar date.

    Raises:
        argparse.ArgumentTypeError: If the value is not a valid ISO date.
    """
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD format.") from exc


def _select_account_uid(accounts: list[Any]) -> str:
    """Select an account UID without printing account identifiers.

    Args:
        accounts (list[Any]): Account resources returned by Enable Banking.

    Returns:
        str: UID of the selected account.

    Raises:
        EnableBankingError: If an account is missing its UID.
    """
    if not accounts:
        raise EnableBankingError("The authorized session contains no accounts.")

    account_uids: list[str] = []
    for account in accounts:
        if not isinstance(account, dict):
            raise EnableBankingError("Enable Banking returned an invalid account.")
        uid = account.get("uid")
        if not isinstance(uid, str) or not uid.strip():
            raise EnableBankingError("An account response is missing its UID.")
        account_uids.append(uid)

    if len(account_uids) == 1:
        print("Using the only linked account.")
        return account_uids[0]

    for index, account in enumerate(accounts, start=1):
        currency = account.get("currency")
        currency_label = f" ({currency})" if isinstance(currency, str) else ""
        print(f"{index}. Account {index}{currency_label}")

    while True:
        choice = input("Choose an account number: ")
        try:
            selected_index = int(choice) - 1
        except ValueError:
            print("Enter a number from the account list.")
            continue
        if 0 <= selected_index < len(account_uids):
            return account_uids[selected_index]
        print("Choose a number from the account list.")


def main(argv: list[str] | None = None) -> None:
    """Authorize a bank account and synchronize its transactions to SQLite.

    Args:
        argv (list[str] | None): Optional command-line arguments for testing.
    """
    parser = argparse.ArgumentParser(
        description="Authorize a bank account through a local Enable Banking callback."
    )
    parser.add_argument("--bank", required=True, help="Enable Banking ASPSP name")
    parser.add_argument("--country", default="PL", help="Two-letter country code")
    parser.add_argument(
        "--date-from",
        required=True,
        type=_parse_iso_date,
        help="First transaction date to fetch (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--date-to",
        required=True,
        type=_parse_iso_date,
        help="Last transaction date to fetch (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/expenses.db"),
        help="SQLite database path (default: data/expenses.db)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Callback timeout in seconds (default: 300)",
    )
    args = parser.parse_args(argv)
    if args.date_from > args.date_to:
        parser.error("--date-from must not be after --date-to.")

    client = EnableBankingClient.from_environment()
    session = authorize_bank(
        client,
        args.bank,
        args.country,
        timeout_seconds=args.timeout,
    )
    account_uid = _select_account_uid(session["accounts"])
    with closing(connect(args.database)) as conn:
        fetched_count = sync_account_range(
            conn,
            client,
            account_uid,
            args.date_from,
            args.date_to,
        )
        stored_count = len(load_transactions(conn, account_uid))
    print(
        f"Fetched {fetched_count} transaction(s); "
        f"database has {stored_count} for this account."
    )


if __name__ == "__main__":
    main()
