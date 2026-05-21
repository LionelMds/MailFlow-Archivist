from __future__ import annotations

from datetime import datetime

from mailflow.classifier.decision_engine import destination_for
from mailflow.classifier.rules_classifier import classify_mail
from mailflow.models import Direction, InterlocutorType, MailMetadata, MailType


def sent_mail(subject: str, body: str = "", recipients: list[str] | None = None) -> MailMetadata:
    return MailMetadata(
        entry_id=f"ENTRY-{subject}",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.SENT,
        subject=subject,
        sender_name="Lionel",
        sender_email="lionel@balzmetal.ch",
        recipients=recipients or ["sales@supplier.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        body_excerpt=body,
    )


def test_sent_demande_offre_to_external_recipient_is_supplier_rfq() -> None:
    result = classify_mail(sent_mail("Demande d'offre de prix - NREF-2025-4893-LMDS"))

    assert result.suggested_type == MailType.DEMANDE_DE_PRIX
    assert result.suggested_interlocutor == InterlocutorType.FOURNISSEUR
    assert destination_for(result.suggested_type, result.suggested_interlocutor) == (
        "DEMANDE DE PRIX"
    )


def test_sent_commande_typo_to_external_recipient_is_supplier_order() -> None:
    result = classify_mail(sent_mail("Comande - GYSO - NREF-2025-4893-LMDS"))

    assert result.suggested_type == MailType.COMMANDE
    assert result.suggested_interlocutor == InterlocutorType.FOURNISSEUR
    assert destination_for(result.suggested_type, result.suggested_interlocutor) == (
        "COMMANDE"
    )


def test_sent_reply_with_order_body_wins_over_original_rfq_subject() -> None:
    result = classify_mail(
        sent_mail(
            "Re: Demande d'offre de prix - Profils",
            body="Bonjour,\nJe vous confirme la commande.\nMerci.",
        )
    )

    assert result.suggested_type == MailType.COMMANDE
    assert result.suggested_interlocutor == InterlocutorType.FOURNISSEUR


def test_sent_demande_etude_to_external_recipient_is_technical_external() -> None:
    result = classify_mail(sent_mail("Demande d'etude statique - CD-1 - NREF-2025-4893-LMDS"))

    assert result.suggested_type == MailType.TECHNIQUE
    assert result.suggested_interlocutor == InterlocutorType.INTERVENANT_EXTERNE
    assert destination_for(result.suggested_type, result.suggested_interlocutor) == "CORRESPONDANCE"


def test_sent_mail_to_internal_recipient_stays_internal() -> None:
    result = classify_mail(
        sent_mail(
            "Demande d'offre de prix - NREF-2025-4893-LMDS",
            recipients=["bureau@balzmetal.ch"],
        )
    )

    assert result.suggested_interlocutor == InterlocutorType.INTERNE
