"""Minimal client for the Monobank personal API (https://api.monobank.ua/docs/)."""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.monobank.ua"
MAX_RANGE_SECONDS = 31 * 24 * 3600 + 3600  # API limit: 31 days + 1 hour


class MonobankError(Exception):
    """Raised when the Monobank API returns an error."""


class MonobankClient:
    def __init__(self, token: str | None = None):
        """Create a client using an explicit token or MONOBANK_TOKEN.

        Args:
            token (str | None): Optional API token; the environment is used if omitted.

        Raises:
            MonobankError: If no token is provided or configured.
        """
        self.token = token or os.environ.get("MONOBANK_TOKEN")
        if not self.token:
            raise MonobankError("MONOBANK_TOKEN is not set (check your .env file)")

    def _get(self, path: str):
        """Send an authenticated GET request to a Monobank endpoint.

        Args:
            path (str): API path appended to the Monobank base URL.

        Returns:
            Any: Decoded JSON response from the API.

        Raises:
            MonobankError: If the API rate limit is reached or the response is unsuccessful.
            requests.RequestException: If the HTTP request fails.
        """
        response = requests.get(
            f"{BASE_URL}{path}",
            headers={"X-Token": self.token},
            timeout=15,
        )
        if response.status_code == 429:
            raise MonobankError("Rate limit hit (1 request/60 s). Wait a minute and retry.")
        if not response.ok:
            raise MonobankError(f"HTTP {response.status_code}: {response.text}")
        return response.json()

    def client_info(self) -> dict:
        """Return information about the token's Monobank client.

        Returns:
            dict: Client information returned by the API.

        Raises:
            MonobankError: If the API returns an unsuccessful response.
            requests.RequestException: If the HTTP request fails.
        """
        return self._get("/personal/client-info")

    def statement(self, date_from: int, date_to: int, account: str = "0") -> list[dict]:
        """Fetch a statement for one account and time range.

        Args:
            date_from (int): Inclusive range start as a Unix timestamp.
            date_to (int): Range end as a Unix timestamp.
            account (str): Account identifier; defaults to the first account.

        Returns:
            list[dict]: Statement records returned by the API.

        Raises:
            ValueError: If the requested range exceeds the API limit.
            MonobankError: If the API returns an unsuccessful response.
            requests.RequestException: If the HTTP request fails.
        """
        if date_to - date_from > MAX_RANGE_SECONDS:
            raise ValueError("Statement range cannot exceed 31 days + 1 hour.")
        return self._get(f"/personal/statement/{account}/{date_from}/{date_to}")


if __name__ == "__main__":
    from personal_expense_analytics_engine.monobank_mapper import (
        monobank_item_to_transaction,
    )

    client = MonobankClient()
    account_currency = os.environ.get("MONOBANK_CURRENCY")
    if not account_currency:
        raise MonobankError("MONOBANK_CURRENCY is not set.")
    now = int(time.time())
    for item in client.statement(now - 30 * 24 * 3600, now)[:5]:
        transaction = monobank_item_to_transaction(
            item,
            account_currency=account_currency,
        )
        print(
            transaction.date,
            transaction.description,
            transaction.signed_amount,
            transaction.currency,
        )