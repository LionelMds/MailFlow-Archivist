from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

from mailflow.classifier.ai_classifier import AiClassifier
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
        archive=True,
        usefulness="normal",
        mail_type="devis",
        interlocutor="fournisseur",
        target_folder="Fournisseurs/Demande de prix",
        confidence=0.88,
        short_summary="Offre fournisseur.",
        reason="Le sujet et les pieces jointes indiquent une offre.",
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
        recipients=["lionel@balzmetal.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=["offre.xlsx"],
        body_excerpt="Bonjour +41 22 123 45 67. Voici le texte.",
    )

    result = classifier.classify(mail, privacy_mask_phone_numbers=True)

    assert result == parsed
    assert responses.kwargs is not None
    assert responses.kwargs["model"] == "gpt-5.4-nano"
    assert responses.kwargs["text_format"] is AiMailClassification
    user_payload = responses.kwargs["input"][1]["content"]
    assert '"project_number": "2025-4893"' in user_payload
    assert "offre.xlsx" in user_payload
    assert "+41 22" not in user_payload
