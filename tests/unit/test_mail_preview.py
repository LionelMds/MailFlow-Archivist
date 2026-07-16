from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.models import (
    AiMailClassification,
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)
from mailflow.ui.mail_preview import (
    ai_decision_html,
    classification_highlight_terms,
    preview_row_to_html,
)


def make_row(tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre garde-corps",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=["Offerte.pdf"],
        body_excerpt="Bonjour, voici notre offre pour le projet.",
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="Decision issue des regles locales.",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                confidence=0.0,
            ),
            ai=AiMailClassification(
                category="Demande de prix",
                organization_role="fournisseur",
                organization_name="Dupont",
                confidence=0.9,
                requires_review=False,
                short_summary="Offre fournisseur.",
                reason="Le sens de l'echange correspond a la consultation.",
                evidence=["offre"],
            ),
        ),
        decision=decision,
        action=PreviewAction.ARCHIVE,
    )


def test_preview_row_to_html_highlights_classification_terms(tmp_path: Path) -> None:
    html = preview_row_to_html(make_row(tmp_path))

    assert "background-color" in html
    assert "Offre" in html
    assert "Raison:" in html


def test_classification_highlight_terms_deduplicates_terms(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    row = row.model_copy(
        update={
            "classification": row.classification.model_copy(
                    update={"ai": row.classification.ai.model_copy(
                        update={"evidence": ["offre", "Offre", "offerte"]}
                    ) if row.classification.ai else None}
            )
        }
    )

    assert classification_highlight_terms(row) == ["offre", "offerte"]


def test_preview_row_to_html_shows_ai_decision_when_available(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    ai = AiMailClassification(
        category="Demande de prix",
        organization_role="fournisseur",
        organization_name="Dupont",
        confidence=0.88,
        requires_review=False,
        short_summary="Offre fournisseur.",
        reason="Le sujet indique une offre.",
        evidence=["offre"],
    )
    row = row.model_copy(
        update={"classification": row.classification.model_copy(update={"ai": ai})}
    )

    rendered = preview_row_to_html(row)

    assert "Decision IA:" in rendered
    assert "Offre fournisseur." in rendered
    assert "Le sujet indique une offre." in rendered


def test_ai_decision_html_says_when_ai_was_not_called(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    without_ai = row.model_copy(
        update={"classification": row.classification.model_copy(update={"ai": None})}
    )

    assert "IA non appelee" in ai_decision_html(without_ai)
