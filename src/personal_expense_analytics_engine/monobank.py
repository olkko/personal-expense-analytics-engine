"""Minimal client for the Monobank personal API (https://api.monobank.ua/docs/)."""

import os
import time

import requests
from dotenv import load_dotenv

from personal_expense_analytics_engine.monobank_mapper import (
    monobank_item_to_transaction,
)

load_dotenv()

BASE_URL = "https://api.monobank.ua"
MAX_RANGE_SECONDS = 31 * 24 * 3600 + 3600  # API limit: 31 days + 1 hour
CURRENCIES = {980: "UAH", 985: "PLN", 840: "USD", 978: "EUR"}


class MonobankError(Exception):
    """Raised when the Monobank API returns an error."""


class MonobankClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("MONOBANK_TOKEN")
        if not self.token:
            raise MonobankError("MONOBANK_TOKEN is not set (check your .env file)")

    def _get(self, path: str):
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
        return self._get("/personal/client-info")

    def statement(self, date_from: int, date_to: int, account: str = "0") -> list[dict]:
        if date_to - date_from > MAX_RANGE_SECONDS:
            raise ValueError("Statement range cannot exceed 31 days")
        return self._get(f"/personal/statement/{account}/{date_from}/{date_to}")


if __name__ == "__main__":
    client = MonobankClient()
    now = int(time.time())
    for item in client.statement(now - 30 * 24 * 3600, now)[:5]:
        transaction = monobank_item_to_transaction(item)
        print(
            item["time"],
            item["description"],
            transaction.signed_amount,
            CURRENCIES.get(item["currencyCode"], item["currencyCode"]),
        )
