from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from mailflow.classifier.prompt import SYSTEM_PROMPT, build_ai_payload
from mailflow.models import AiMailClassification, Direction, MailMetadata


class ResponsesClient(Protocol):
    def parse(self, **kwargs: Any) -> Any:
        ...


class OpenAiClient(Protocol):
    responses: ResponsesClient


@dataclass(frozen=True)
class AiConnectionCheck:
    ok: bool
    message: str
    classification: AiMailClassification | None = None


class AiClassifier:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.4-nano",
        client: OpenAiClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any = client

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        payload = build_ai_payload(
            mail,
            include_body=include_body,
            privacy_mask_phone_numbers=privacy_mask_phone_numbers,
            known_context=known_context,
        )
        response = self._client_or_create().responses.parse(
            model=self._model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            text_format=AiMailClassification,
        )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, AiMailClassification):
            msg = "OpenAI response did not contain a parsed AiMailClassification"
            raise TypeError(msg)
        return parsed

    def check_connection(self) -> AiConnectionCheck:
        try:
            classification = self.classify(_connection_test_mail(), include_body=False)
        except Exception as exc:
            return AiConnectionCheck(
                ok=False,
                message=_safe_error_message(exc, secret=self._api_key),
            )
        return AiConnectionCheck(
            ok=True,
            message=(
                "Connexion OpenAI OK "
                f"({classification.mail_type}, {classification.confidence:.0%})."
            ),
            classification=classification,
        )

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except Exception as exc:
            msg = "The openai package is required when AI classification is enabled"
            raise RuntimeError(msg) from exc
        self._client = OpenAI(api_key=self._api_key)
        return self._client


def _connection_test_mail() -> MailMetadata:
    return MailMetadata(
        entry_id="MAILFLOW-OPENAI-CONNECTION-TEST",
        project_number="2026-0000",
        outlook_folder="Test MailFlow",
        direction=Direction.RECEIVED,
        subject="Test MailFlow Archivist - demande de prix",
        sender_name="MailFlow",
        sender_email="test@example.invalid",
        recipients=["mailflow@example.invalid"],
        sent_at=datetime(2026, 1, 1, 12, 0),
        attachment_names=["test-offre.pdf"],
        body_excerpt="",
    )


def _safe_error_message(exc: Exception, *, secret: str) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if secret:
        message = message.replace(secret, "[cle masquee]")
    if len(message) > 220:
        message = f"{message[:217]}..."
    return f"Echec OpenAI: {message}"
