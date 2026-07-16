from __future__ import annotations

import pytest
from pydantic import ValidationError

from mailflow.models import AiMailClassification


def valid_output(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "category": "Commande",
        "organization_role": "fournisseur",
        "organization_name": "Metal Factory",
        "confidence": 0.91,
        "requires_review": False,
        "short_summary": "Commande fournisseur a archiver.",
        "reason": "L'echange confirme un engagement d'achat et son suivi.",
        "evidence": ["Nous confirmons la commande"],
    }
    result.update(updates)
    return result


def test_ai_schema_accepts_only_the_three_standard_categories() -> None:
    parsed = AiMailClassification.model_validate(valid_output())

    assert parsed.category == "Commande"
    assert parsed.target_folder == "Fournisseurs/Commande"
    assert parsed.mail_type == "commande"


@pytest.mark.parametrize("category", ["devis", "plan", "facture", "A verifier"])
def test_ai_schema_rejects_legacy_categories(category: str) -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(valid_output(category=category))


def test_ai_schema_forces_review_for_low_confidence() -> None:
    parsed = AiMailClassification.model_validate(
        valid_output(confidence=0.5, requires_review=False)
    )

    assert parsed.requires_review
    assert parsed.target_folder == "A verifier"
    assert not parsed.archive


def test_ai_schema_forces_review_for_unknown_role() -> None:
    parsed = AiMailClassification.model_validate(
        valid_output(organization_role="inconnu", requires_review=False)
    )

    assert parsed.requires_review


def test_ai_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(valid_output(body="should not be accepted"))


def test_ai_schema_limits_evidence_to_three_short_excerpts() -> None:
    with pytest.raises(ValidationError):
        AiMailClassification.model_validate(valid_output(evidence=["a", "b", "c", "d"]))
