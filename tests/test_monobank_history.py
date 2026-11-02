from personal_expense_analytics_engine.monobank_history import fetch_history


class FakeClient:
    def __init__(self):
        self.calls = []

    def statement(self, date_from, date_to, account="0"):
        self.calls.append((date_from, date_to))
        # One item per call; the same id repeats to test deduplication
        return [{"id": "same", "time": date_to - 10}, {"id": f"id-{date_from}", "time": date_from + 10}]


def test_fetch_history_splits_into_windows_and_deduplicates():
    client = FakeClient()
    items = fetch_history(client, days=75, pause=0, now=10_000_000)
    assert len(client.calls) == 3  # 75 days -> 30 + 30 + 15
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    assert [i["time"] for i in items] == sorted((i["time"] for i in items), reverse=True)
