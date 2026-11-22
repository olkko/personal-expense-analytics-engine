"""Command-line entry point for locally generated monthly spending insights."""

import argparse
from contextlib import closing
from pathlib import Path

from personal_expense_analytics_engine._analyst import monthly_spending_insight
from personal_expense_analytics_engine._storage import connect


def main(argv: list[str] | None = None) -> None:
    """Generate an Ollama narrative from transactions already stored in SQLite.

    Args:
        argv (list[str] | None): Optional command-line arguments for testing.
    """
    parser = argparse.ArgumentParser(
        description="Analyze a stored month using the local Ollama model."
    )
    parser.add_argument("--month", required=True, help="Month to analyze (YYYY-MM)")
    parser.add_argument("--currency", required=True, help="Currency code, e.g. PLN")
    parser.add_argument(
        "--account-id",
        help="Optional account ID filter; omitted means all accounts",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/expenses.db"),
        help="SQLite database path (default: data/expenses.db)",
    )
    parser.add_argument(
        "--model",
        default="qwen3:0.6b",
        help="Installed Ollama model (default: qwen3:0.6b)",
    )
    args = parser.parse_args(argv)

    with closing(connect(args.database)) as conn:
        insight = monthly_spending_insight(
            conn,
            args.month,
            args.currency,
            account_id=args.account_id,
            model=args.model,
        )

    print(f"AI analyst ({insight.model}):\n{insight.text}")
    print(f"\nVerified figures:\n{insight.facts_text}")


if __name__ == "__main__":
    main()
