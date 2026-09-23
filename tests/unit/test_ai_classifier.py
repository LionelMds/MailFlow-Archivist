from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import OpenAI

from mailflow.classifier.ai_classifier import (
    AiClassifier,
    AiResponseError,
    _connection_test_mail,
    _safe_error_message,
)
from mailflow.classifier.prompt import build_ai_payload
from mailflow.models import AiMailClassification, Direction, MailMetadata


class FakeResponses:
    def __init__(self, parsed: AiMailClassification) -> None:
        self.parsed = parsed
        self.kwargs: dict[str, Any] | None = None

    def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=self.parsed)


def test_ai_classifier_uses_structured_output_and_metadata_only() -> None:
    parsed = AiMailClassification(
        category="Demande de prix",
        organization_role="fournisseur",
        organization_name="Dupont",
        confidence=0.88,
        requires_review=False,
        short_summary="Offre fournisseur.",
        reason="Le sujet et les pieces jointes indiquent une offre.",
        evidence=["offre fournisseur"],
    )
    responses = FakeResponses(parsed)
    client = SimpleNamespace(responses=responses)
    classifier = AiClassifier(api_key="sk-test", client=client)
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sender_email="dupont@example.com",
        recipients=["lionel@balzmetal.ch"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=["offre.xlsx"],
        body_excerpt="Bonjour +41 22 123 45 67. Voici le texte.",
    )

    result = classifier.classify(mail, privacy_mask_phone_numbers=True)

    assert result == parsed
    assert responses.kwargs is not None
    assert responses.kwargs["model"] == "gpt-6-astra"
    assert responses.kwargs["reasoning"] == {"effort": "low"}
    assert responses.kwargs["store"] is False
    assert responses.kwargs["text_format"] is AiMailClassification
    user_payload = responses.kwargs["input"][1]["content"]
    assert '"project_number": "2025-4893"' in user_payload
    assert "offre.xlsx" in user_payload
    assert '"primary_recipient": "lionel@balzmetal.ch"' in user_payload
    assert '"primary_recipient_is_internal": true' in user_payload
    assert "+41 22" not in user_payload


def test_ai_classifier_connection_check_uses_synthetic_structured_request() -> None:
    parsed = AiMailClassification(
        category="Demande de prix",
        organization_role="fournisseur",
        organization_name="MailFlow Test",
        confidence=0.91,
        requires_review=False,
        short_summary="Test OK.",
        reason="Le test retourne une classification structuree.",
        evidence=["test"],
    )
    responses = FakeResponses(parsed)
    classifier = AiClassifier(api_key="sk-test-secret", client=SimpleNamespace(responses=responses))

    check = classifier.check_connection()

    assert check.ok
    assert check.classification == parsed
    assert "Connexion OpenAI OK" in check.message
    assert responses.kwargs is not None
    user_payload = responses.kwargs["input"][1]["content"]
    assert "MAILFLOW-OPENAI-CONNECTION-TEST" not in user_payload
    assert "demande de prix" in user_payload


def test_ai_error_message_redacts_api_key() -> None:
    message = _safe_error_message(RuntimeError("bad sk-test-secret"), secret="sk-test-secret")

    assert "sk-test-secret" not in message
    assert "[cle masquee]" in message


def _classification_payload() -> dict[str, Any]:
    return {
        "category": "Demande de prix",
        "organization_role": "fournisseur",
        "organization_name": "Test fournisseur",
        "confidence": 0.91,
        "requires_review": False,
        "short_summary": "Consultation fournisseur.",
        "reason": "Le mail demande un prix.",
        "evidence": ["demande de prix"],
    }


def _sdk_response(
    *, status: str = "completed", content: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "resp_synthetic_test",
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": "gpt-6-astra",
        "output": [{
            "id": "msg_synthetic_test", "type": "message", "role": "assistant",
            "status": "completed",
            "content": [content or {
                "type": "output_text", "annotations": [],
                "text": json.dumps(_classification_payload()),
            }],
        }],
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


def test_astra_request_and_schema_pass_through_real_sdk_without_network() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_sdk_response())

    with OpenAI(
        api_key="synthetic-test-key", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        result = AiClassifier(api_key="synthetic-test-key", client=client).classify(
            _connection_test_mail(), include_body=False,
        )

    assert result == AiMailClassification.model_validate(_classification_payload())
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "gpt-6-astra"
    assert request["reasoning"] == {"effort": "low"}
    assert request["store"] is False
    assert request["max_output_tokens"] == 4096
    assert not {"temperature", "top_p", "logprobs", "top_logprobs"} & request.keys()
    output_format = request["text"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is True
    assert output_format["schema"]["additionalProperties"] is False
    assert set(output_format["schema"]["required"]) == set(AiMailClassification.model_fields)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_sdk_response(status="incomplete"), "incomplete"),
        (_sdk_response(status="failed"), "interrompue"),
        (_sdk_response(content={"type": "refusal", "refusal": "synthetic"}), "refuse"),
        (_sdk_response(content={
            "type": "output_text", "annotations": [], "text": '{"private":"mail excerpt"}',
        }), "format attendu"),
    ],
)
def test_invalid_sdk_responses_remain_for_review(
    response: dict[str, Any], message: str,
) -> None:
    with OpenAI(
        api_key="synthetic-test-key", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response),
        )),
    ) as client:
        classifier = AiClassifier(api_key="synthetic-test-key", client=client)
        with pytest.raises(AiResponseError, match=message) as error:
            classifier.classify(_connection_test_mail())
        assert "mail excerpt" not in str(error.value)


@pytest.mark.parametrize("model", ["gpt-4o-mini", "custom-model-id"])
def test_custom_models_do_not_receive_astra_reasoning(model: str) -> None:
    responses = FakeResponses(AiMailClassification.model_validate(_classification_payload()))
    classifier = AiClassifier(
        api_key="synthetic-test-key", model=model, client=SimpleNamespace(responses=responses),
    )

    classifier.classify(_connection_test_mail())

    assert responses.kwargs is not None
    assert responses.kwargs["model"] == model
    assert "reasoning" not in responses.kwargs
    assert responses.kwargs["store"] is False


def test_recipient_priority_skips_internal_copies_before_external_recipient() -> None:
    mail = _connection_test_mail().model_copy(update={
        "direction": Direction.SENT,
        "recipients": ["collegue@balzmetal.ch", "Fournisseur <sales@example.invalid>"],
    })

    payload = build_ai_payload(mail, include_body=False, privacy_mask_phone_numbers=False)

    assert payload["recipient_priority"]["primary_recipient"] == (
        "Fournisseur <sales@example.invalid>"
    )
    assert payload["recipient_priority"]["primary_recipient_is_internal"] is False
    assert payload["body_excerpt"] == ""


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6-luna", "gpt-6-luna-2026-09-01"])
def test_gpt6_models_use_low_reasoning(model: str) -> None:
    responses = FakeResponses(AiMailClassification.model_validate(_classification_payload()))
    classifier = AiClassifier(
        api_key="synthetic-test-key", model=model, client=SimpleNamespace(responses=responses),
    )

    classifier.classify(_connection_test_mail())

    assert responses.kwargs is not None
    assert responses.kwargs["reasoning"] == {"effort": "low"}


def test_lunar_lookalike_models_do_not_receive_reasoning() -> None:
    responses = FakeResponses(AiMailClassification.model_validate(_classification_payload()))
    classifier = AiClassifier(
        api_key="synthetic-test-key", model="gpt-6-lunatic",
        client=SimpleNamespace(responses=responses),
    )

    classifier.classify(_connection_test_mail())

    assert responses.kwargs is not None
    assert "reasoning" not in responses.kwargs
