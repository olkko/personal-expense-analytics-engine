"""Command-line entry point for rule-based and Ollama transaction categorization."""

import argparse
import logging
from contextlib import closing
from pathlib import Path

from personal_expense_analytics_engine._ollama import (
    DEFAULT_CATEGORY_CONFIDENCE,
    DEFAULT_MODEL,
)
from personal_expense_analytics_engine._processing import (
    StoredCategoryExplanation,
    categorize_stored_with_ollama,
    explain_uncategorized_stored,
)
from personal_expense_analytics_engine._storage import connect


def main(argv: list[str] | None = None) -> None:
    """Categorize existing uncategorized rows and report the resulting counts.

    Args:
        argv (list[str] | None): Optional command-line arguments for testing.
    """
    parser = argparse.ArgumentParser(
        description="Categorize stored transactions with rules and local Ollama."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/expenses.db"),
        help="SQLite database path (default: data/expenses.db)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Installed Ollama model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_CATEGORY_CONFIDENCE,
        help=(
            "Minimum confidence for saving an AI category "
            f"(default: {DEFAULT_CATEGORY_CONFIDENCE})"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show rule and AI stage counts without printing transaction details",
    )
    parser.add_argument(
        "--explain-uncategorized",
        action="store_true",
        help="Explain each uncategorized row without saving any changes",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.min_confidence <= 1:
        parser.error("--min-confidence must be between 0 and 1.")
    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    explanations: list[StoredCategoryExplanation] = []
    counts: dict[str, int] = {}
    with closing(connect(args.database)) as conn:
        if args.explain_uncategorized:
            explanations = explain_uncategorized_stored(
                conn,
                model=args.model,
                min_confidence=args.min_confidence,
            )
        else:
            counts = categorize_stored_with_ollama(
                conn,
                model=args.model,
                min_confidence=args.min_confidence,
            )

    if args.explain_uncategorized:
        if not explanations:
            print("No Uncategorized transactions found.")
            return
        print("Uncategorized explanations (read-only; nothing was saved):")
        for explanation in explanations:
            print(f"\n{explanation.date} | {explanation.description}")
            if explanation.merchant:
                print(f"  Merchant: {explanation.merchant}")
            print(
                f"  MCC: {explanation.mcc if explanation.mcc is not None else 'not provided'}"
                f"; direction: {explanation.direction}"
            )
            if explanation.rule_category is not None:
                print(f"  Rule match: would categorize as {explanation.rule_category}.")
                continue

            prediction = explanation.ai_prediction
            if prediction is None:
                continue
            if prediction.accepted:
                print(
                    f"  Suggestion: {prediction.category} "
                    f"({prediction.confidence:.0%} confidence; meets threshold)."
                )
            elif prediction.category == "Uncategorized":
                print(
                    "  Suggestion: Uncategorized "
                    f"({prediction.confidence:.0%} model confidence)."
                )
            else:
                print(
                    f"  Suggestion: {prediction.category} "
                    f"({prediction.confidence:.0%} confidence; below the "
                    f"{args.min_confidence:.0%} threshold)."
                )
            print(f"  AI explanation (not verified): {prediction.reason}")
        return

    if not counts:
        print("No uncategorized transactions found.")
        return
    print("Categorization results:")
    for category, count in sorted(counts.items()):
        print(f"- {category}: {count}")


if __name__ == "__main__":
    main()
