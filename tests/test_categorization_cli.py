from unittest.mock import Mock, patch

from personal_expense_analytics_engine._categorization_cli import main
from personal_expense_analytics_engine._ollama import CategoryPrediction
from personal_expense_analytics_engine._processing import (
    StoredCategoryExplanation,
)


def test_explain_uncategorized_cli_prints_reason_without_categorizing(capsys):
    explanation = StoredCategoryExplanation(
        date="2026-10-01",
        description="Unknown merchant",
        merchant="Unknown",
        mcc=None,
        direction="debit",
        rule_category=None,
        ai_prediction=CategoryPrediction(
            category="Dining",
            confidence=0.6,
            reason="The description does not identify a restaurant.",
            accepted=False,
        ),
    )
    connection = Mock()

    with (
        patch(
            "personal_expense_analytics_engine._categorization_cli.connect",
            return_value=connection,
        ),
        patch(
            "personal_expense_analytics_engine._categorization_cli.explain_uncategorized_stored",
            return_value=[explanation],
        ),
        patch(
            "personal_expense_analytics_engine._categorization_cli.categorize_stored_with_ollama"
        ) as categorize,
    ):
        main(["--database", "ignored.db", "--explain-uncategorized"])

    output = capsys.readouterr().out
    assert "Unknown merchant" in output
    assert "below the 75% threshold" in output
    assert "AI explanation (not verified)" in output
    assert "does not identify a restaurant" in output
    assert "nothing was saved" in output
    categorize.assert_not_called()
    connection.close.assert_called_once()
