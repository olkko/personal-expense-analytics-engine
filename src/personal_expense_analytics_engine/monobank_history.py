"""Fetch longer Monobank statement history within API limits."""

import time
from typing import Any, Protocol

WINDOW_SECONDS = 30 * 24 * 3600
PAGE_LIMIT = 500          # API returns at most 500 items per request
REQUEST_PAUSE = 61        # API allows 1 request per 60 seconds


class StatementClient(Protocol):
    """Client interface needed to fetch paginated statement history."""

    def statement(
        self,
        date_from: int,
        date_to: int,
        account: str = "0",
    ) -> list[dict[str, Any]]:
        """Return statement items for an account and time range.

        Args:
            date_from (int): Inclusive range start as a Unix timestamp.
            date_to (int): Range end as a Unix timestamp.
            account (str): Account identifier.

        Returns:
            list[dict[str, Any]]: Raw statement records.
        """
        ...


def fetch_history(
    client: StatementClient,
    days: int,
    account: str = "0",
    pause: float = REQUEST_PAUSE,
    now: int | None = None,
) -> list[dict]:
    """Fetch raw statement items for recent days, newest first, and deduplicate them.

    Args:
        client (StatementClient): Client used to request statement pages.
        days (int): Number of days of history to fetch.
        account (str): Account identifier.
        pause (float): Delay in seconds between API requests.
        now (int | None): Optional current time as a Unix timestamp.

    Returns:
        list[dict]: Unique raw statement records, newest first.
    """
    end = now if now is not None else int(time.time())
    start = end - days * 24 * 3600
    items_by_id: dict[str, dict] = {}
    first_request = True

    window_to = end
    while window_to > start:
        window_from = max(window_to - WINDOW_SECONDS, start)
        cursor_to = window_to
        while True:
            if not first_request:
                time.sleep(pause)
            first_request = False
            print(f"Fetching {window_from} -> {cursor_to} ...")
            batch = client.statement(window_from, cursor_to, account)
            for item in batch:
                items_by_id[item["id"]] = item
            if len(batch) < PAGE_LIMIT:
                break
            # Page was full: continue from the oldest item we received
            cursor_to = min(i["time"] for i in batch)
        window_to = window_from

    return sorted(items_by_id.values(), key=lambda i: i["time"], reverse=True)
