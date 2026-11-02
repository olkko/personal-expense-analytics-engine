"""Fetch longer Monobank statement history within API limits."""

import time

from personal_expense_analytics_engine.monobank import MonobankClient

WINDOW_SECONDS = 30 * 24 * 3600
PAGE_LIMIT = 500          # API returns at most 500 items per request
REQUEST_PAUSE = 61        # API allows 1 request per 60 seconds


def fetch_history(
    client: MonobankClient,
    days: int,
    account: str = "0",
    pause: float = REQUEST_PAUSE,
    now: int | None = None,
) -> list[dict]:
    """Return raw statement items for the last `days` days, newest first, deduplicated."""
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
