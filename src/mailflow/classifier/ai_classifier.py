from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from mailflow.classifier.prompt import SYSTEM_PROMPT, build_ai_payload
from mailflow.config import DEFAULT_AI_MODEL, DEFAULT_OPENAI_TIMEOUT_SECONDS
from mailflow.models import AiMailClassification, Direction, MailMetadata


class ResponsesClient(Protocol):
    @property
    def parse(self) -> Callable[..., Any]:
        ...


class OpenAiClient(Protocol):
    @property
    def responses(self) -> ResponsesClient:
        ...


class AiResponseError(RuntimeError):
    """A response that must be reviewed instead of used for automatic routing."""


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
        model: str = DEFAULT_AI_MODEL,
        timeout_seconds: float = DEFAULT_OPENAI_TIMEOUT_SECONDS,
        client: OpenAiClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client: Any = client

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        payload = build_ai_payload(
            mail,
            include_body=include_body,
            privacy_mask_phone_numbers=privacy_mask_phone_numbers,
            known_context=known_context,
        )
        request: dict[str, Any] = dict(
            model=self._model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            text_format=AiMailClassification,
            store=False,
            max_output_tokens=4096,
        )
        if self._model == DEFAULT_AI_MODEL or self._model.startswith("gpt-6-astra-"):
            request["reasoning"] = {"effort": "low"}
        try:
            response = self._client_or_create().responses.parse(**request)
        except ValidationError as exc:
            # Pydantic errors can contain the original mail excerpts; don't show them.
            raise AiResponseError("La reponse IA ne respecte pas le format attendu.") from exc
        status = getattr(response, "status", "completed")
        if status != "completed":
            raise AiResponseError(
                "La reponse IA est incomplete ou interrompue. Le mail reste a verifier."
            )
        for output in getattr(response, "output", []):
            for content in getattr(output, "content", []):
                if getattr(content, "type", None) == "refusal":
                    raise AiResponseError(
                        "Le modele a refuse cette classification. Le mail reste a verifier."
                    )
        parsed = getattr(response, "output_parsed", None)
        if not isinstance(parsed, AiMailClassification):
            raise AiResponseError("La reponse IA ne contient aucune classification exploitable.")
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
                f"({classification.category}, {classification.confidence:.0%})."
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
        self._client = OpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=1,
        )
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
