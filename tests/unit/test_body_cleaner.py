from __future__ import annotations

from mailflow.core.body_cleaner import clean_body


def test_clean_body_removes_signature_and_quoted_thread() -> None:
    body = """Bonjour,

Voici notre offre en retour.

Cordialement,
Jean

De : ancien message
texte cite
"""

    cleaned = clean_body(body)

    assert "Voici notre offre" in cleaned
    assert "Cordialement" not in cleaned
    assert "ancien message" not in cleaned


def test_clean_body_masks_phone_numbers_when_enabled() -> None:
    cleaned = clean_body("Appelez-moi au +41 22 123 45 67 demain.", mask_phone_numbers=True)

    assert "+41" not in cleaned
    assert "[telephone masque]" in cleaned


def test_clean_body_limits_excerpt() -> None:
    assert clean_body("x" * 9000, limit=8000) == "x" * 8000

