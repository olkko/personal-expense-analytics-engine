import json as jsonlib
from unittest.mock import Mock, patch

import pytest
import requests

from personal_expense_analytics_engine._ollama import (
    CategoryPrediction,
    OllamaAnalystError,
    categorize_transactions,
    generate_narrative,
)


def test_generate_narrative_sends_only_evidence_to_local_model():
    response = Mock()
    response.json.return_value = {"message": {"content": "Spending rose this month."}}

    with patch(
        "personal_expense_analytics_engine._ollama.requests.post", return_value=response
    ) as post:
        narrative = generate_narrative(
            "Spending increased by 10% compared with last month.",
            model="test-model",
        )

    assert narrative == "Spending rose this month."
    post.assert_called_once()
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url == "http://127.0.0.1:11434/api/chat"
    assert payload["model"] == "test-model"
    assert payload["stream"] is False
    assert payload["messages"][1]["content"] == (
        "Spending increased by 10% compared with last month."
    )
    assert "transactions" not in payload["messages"][1]["content"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com",
        "http://192.168.1.20:11434",
        "http://localhost:11434/?token=secret",
    ],
)
def test_generate_narrative_rejects_non_local_ollama_url(base_url):
    with pytest.raises(ValueError, match="local HTTP service"):
        generate_narrative("Verified facts.", base_url=base_url)


def test_generate_narrative_reports_unavailable_ollama():
    with patch(
        "personal_expense_analytics_engine._ollama.requests.post",
        side_effect=requests.ConnectionError,
    ):
        with pytest.raises(OllamaAnalystError, match="local Ollama service"):
            generate_narrative("Verified facts.")


def test_generate_narrative_rejects_missing_model_response():
    response = Mock()
    response.json.return_value = {"message": {"role": "assistant", "content": ""}}

    with patch(
        "personal_expense_analytics_engine._ollama.requests.post", return_value=response
    ):
        with pytest.raises(OllamaAnalystError, match="omitted its generated message"):
            generate_narrative("Verified facts.")


def test_categorize_transactions_validates_categories_and_confidence():
    response = Mock()
    response.json.return_value = {
        "message": {
            "content": (
                '{"results": ['
                '{"index": 1, "category": "Dining", "confidence": 0.9,'
                '"reason": "The merchant is a cafe."},'
                '{"index": 0, "category": "Groceries", "confidence": 0.8,'
                '"reason": "The MCC indicates a grocery store."}'
                "]}"
            )
        }
    }

    with patch(
        "personal_expense_analytics_engine._ollama.requests.post",
        return_value=response,
    ) as post:
        predictions = categorize_transactions(
            [
                {
                    "description": "Market purchase",
                    "merchant": "Market",
                    "mcc": 5411,
                    "direction": "debit",
                },
                {
                    "description": "Cafe purchase",
                    "merchant": "Cafe",
                    "mcc": None,
                    "direction": "debit",
                },
            ],
            ("Dining", "Groceries"),
        )

    assert predictions == [
        CategoryPrediction(
            category="Groceries",
            confidence=0.8,
            reason="The MCC indicates a grocery store.",
            accepted=True,
        ),
        CategoryPrediction(
            category="Dining",
            confidence=0.9,
            reason="The merchant is a cafe.",
            accepted=True,
        ),
    ]
    sent_prompt = post.call_args.kwargs["json"]["messages"][1]["content"]
    assert "transaction_id" not in sent_prompt
    assert "amount_minor" not in sent_prompt


def test_categorize_transactions_preserves_low_confidence_explanation():
    response = Mock()
    response.json.return_value = {
        "message": {
            "content": (
                '{"results": [{"index": 0, "category": "Dining", "confidence": 0.5,'
                '"reason": "The description is vague."}]}'
            )
        }
    }

    with patch(
        "personal_expense_analytics_engine._ollama.requests.post",
        return_value=response,
    ):
        predictions = categorize_transactions(
            [{"description": "Unknown", "direction": "debit"}],
            ("Dining",),
        )

    assert predictions == [
        CategoryPrediction(
            category="Dining",
            confidence=0.5,
            reason="The description is vague.",
            accepted=False,
        )
    ]


def test_categorize_transactions_rejects_unsupported_category():
    response = Mock()
    response.json.return_value = {
        "message": {
            "content": (
                '{"results": ['
                '{"index": 0, "category": "Investment", "confidence": 0.99'
                ',"reason": "Unsupported."}'
                "]}"
            )
        }
    }

    with (
        patch(
            "personal_expense_analytics_engine._ollama.requests.post",
            return_value=response,
        ),
        pytest.raises(OllamaAnalystError, match="unsupported category"),
    ):
        categorize_transactions(
            [{"description": "Unknown", "direction": "debit"}],
            ("Dining",),
        )


def test_categorize_transactions_batches_large_inputs():
    def post(_url, *, json: dict, timeout: float):
        items = jsonlib.loads(json["messages"][1]["content"])
        response = Mock()
        response.json.return_value = {
            "message": {
                "content": jsonlib.dumps(
                    {
                        "results": [
                            {
                                "index": item["index"],
                                "category": "Dining",
                                "confidence": 0.9,
                                "reason": "The details suggest dining.",
                            }
                            for item in items
                        ]
                    }
                )
            }
        }
        return response

    transactions = [
        {"description": f"Transaction {index}", "direction": "debit"}
        for index in range(9)
    ]
    with patch(
        "personal_expense_analytics_engine._ollama.requests.post",
        side_effect=post,
    ) as request:
        predictions = categorize_transactions(transactions, ("Dining",))

    assert [prediction.category for prediction in predictions] == ["Dining"] * 9
    assert all(prediction.accepted for prediction in predictions)
    assert request.call_count == 2
    assert request.call_args_list[0].kwargs["json"]["think"] is False
    assert request.call_args_list[0].kwargs["json"]["options"] == {
        "temperature": 0,
        "num_predict": 512,
    }


def test_categorize_transactions_retries_incomplete_batch_as_smaller_parts():
    def post(_url, *, json: dict, timeout: float):
        items = jsonlib.loads(json["messages"][1]["content"])
        results = [
            {
                "index": item["index"],
                "category": "Dining",
                "confidence": 0.9,
                "reason": "The details suggest dining.",
            }
            for item in items[:1]
        ]
        response = Mock()
        response.json.return_value = {
            "message": {"content": jsonlib.dumps({"results": results})}
        }
        return response

    transactions = [
        {"description": "First", "direction": "debit"},
        {"description": "Second", "direction": "debit"},
    ]
    with patch(
        "personal_expense_analytics_engine._ollama.requests.post",
        side_effect=post,
    ) as request:
        predictions = categorize_transactions(transactions, ("Dining",))

    assert [prediction.category for prediction in predictions] == ["Dining", "Dining"]
    assert request.call_count == 3
