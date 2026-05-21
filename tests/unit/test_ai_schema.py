from __future__ import annotations

import pytest
from pydantic import ValidationError

from mailflow.models import AiMailClassification


def test_ai_schema_accepts_valid_structured_output() -> None:
    parsed = AiMailClassification.model_validate(
        {
            "archive": True,
            "usefulness": "normal",
            "mail_type": "commande",
            "interlocutor": "fournisseur",
            "target_folder": "Fournisseurs/Commande",
            "confidence": 0.91,
            "short_summary": "Commande fournisseur a archiver.",
            "reason": "Commande explicite avec reference projet.",
        }
    )

    assert parsed.mail_type == "commande"


def test_ai_schema_forces_review_target_for_low_confidence() -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(
            {
                "archive": True,
                "usefulness": "a_verifier",
                "mail_type": "a_verifier",
                "interlocutor": "inconnu",
                "target_folder": "Correspondance",
                "confidence": 0.5,
                "short_summary": "Ambigu.",
                "reason": "Pas assez clair.",
            }
        )


def test_ai_schema_forces_no_archive_when_useless() -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(
            {
                "archive": True,
                "usefulness": "inutile",
                "mail_type": "inutile_ou_faible_valeur",
                "interlocutor": "inconnu",
                "target_folder": "Ne pas archiver",
                "confidence": 0.9,
                "short_summary": "Merci seul.",
                "reason": "Mail sans valeur projet.",
            }
        )


def test_ai_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(
            {
                "archive": False,
                "usefulness": "inutile",
                "mail_type": "inutile_ou_faible_valeur",
                "interlocutor": "inconnu",
                "target_folder": "Ne pas archiver",
                "confidence": 0.9,
                "short_summary": "Merci.",
                "reason": "Faible valeur.",
                "body": "should not be accepted",
            }
        )
