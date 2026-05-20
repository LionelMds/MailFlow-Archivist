from __future__ import annotations

import json
from typing import Any, Protocol

from mailflow.classifier.prompt import SYSTEM_PROMPT, build_ai_payload
from mailflow.models import AiMailClassification, MailMetadata


class ResponsesClient(Protocol):
    def parse(self, **kwargs: Any) -> Any:
        ...


class OpenAiClient(Protocol):
    responses: ResponsesClient


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
