from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.manual_review import (
    LearnedClassificationRule,
    apply_manual_classification,
    classify_with_learned_terms,
    learned_rule_from_signal,
    misleading_term_from_signal,
)
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)


def make_row(tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="A verifier",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=False,
        requires_review=True,
        mail_type=MailType.A_VERIFIER,
        interlocutor=InterlocutorType.INCONNU,
        target_relative_folder="A verifier",
        target_path=tmp_path / "2025" / "2025-4893",
        confidence=0.4,
        duplicate_status="none",
        reason="Ambigu.",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=None,
                suggested_interlocutor=None,
                likely_archive=None,
                confidence=0,
                matched_rules=[],
            )
        ),
        decision=decision,
        action=PreviewAction.REVIEW,
    )


def test_apply_manual_classification_updates_decision_and_learning_signal(
    tmp_path: Path,
) -> None:
    row = make_row(tmp_path)
    update = ManualClassificationUpdate(
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        learning_term="Offerte",
        misleading_term="newsletter",
    )

    updated, signal = apply_manual_classification(row, update, projects_root=tmp_path)

    assert updated.action == PreviewAction.ARCHIVE
    assert updated.decision.archive is True
    assert updated.decision.requires_review is False
    assert updated.decision.target_path == (
        tmp_path / "2025" / "2025-4893" / "Fournisseurs" / "Demande de prix"
    )
    assert signal.learning_term == "Offerte"
    assert signal.misleading_term == "newsletter"
    assert signal.manual_required is False


def test_apply_manual_classification_records_manual_required_without_term(
    tmp_path: Path,
) -> None:
    update = ManualClassificationUpdate(
        mail_type=MailType.CORRESPONDANCE_GENERALE,
        interlocutor=InterlocutorType.CLIENT,
        target_relative_folder="Correspondance",
        learning_term=" ",
        manual_required=True,
    )

    updated, signal = apply_manual_classification(
        make_row(tmp_path),
        update,
        projects_root=tmp_path,
    )

    assert updated.action == PreviewAction.ARCHIVE
    assert signal.learning_term is None
    assert signal.manual_required is True


def test_apply_manual_classification_keeps_a_verifier_in_review(tmp_path: Path) -> None:
    update = ManualClassificationUpdate(
        mail_type=MailType.A_VERIFIER,
        interlocutor=InterlocutorType.INCONNU,
        target_relative_folder="A verifier",
        manual_required=True,
    )

    updated, _signal = apply_manual_classification(
        make_row(tmp_path),
        update,
        projects_root=tmp_path,
    )

    assert updated.action == PreviewAction.REVIEW
    assert updated.decision.archive is False


def test_apply_manual_classification_rejects_unknown_destination(tmp_path: Path) -> None:
    update = ManualClassificationUpdate(
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Foo",
    )

    with pytest.raises(ValueError):
        apply_manual_classification(make_row(tmp_path), update, projects_root=tmp_path)


def test_learned_rule_from_signal_ignores_manual_required(tmp_path: Path) -> None:
    _updated, signal = apply_manual_classification(
        make_row(tmp_path),
        ManualClassificationUpdate(
            mail_type=MailType.DEVIS,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder="Fournisseurs/Demande de prix",
            learning_term="Offerte",
            manual_required=True,
        ),
        projects_root=tmp_path,
    )

    assert learned_rule_from_signal(signal) is None


def test_misleading_term_from_signal_records_negative_learning(tmp_path: Path) -> None:
    _updated, signal = apply_manual_classification(
        make_row(tmp_path),
        ManualClassificationUpdate(
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=InterlocutorType.CLIENT,
            target_relative_folder="Correspondance",
            misleading_term="offre",
        ),
        projects_root=tmp_path,
    )

    term = misleading_term_from_signal(signal)

    assert term is not None
    assert term.term == "offre"


def test_classify_with_learned_terms_matches_subject(tmp_path: Path) -> None:
    mail = make_row(tmp_path).mail.model_copy(update={"subject": "Mot special 123"})
    rule = LearnedClassificationRule(
        term="mot special",
        mail_type=MailType.ADMINISTRATIF,
        interlocutor=InterlocutorType.CLIENT,
        target_relative_folder="Correspondance",
    )

    result = classify_with_learned_terms(mail, [rule])

    assert result is not None
    assert result.suggested_type == MailType.ADMINISTRATIF
    assert result.matched_rules == ["apprentissage:mot special"]
    assert result.matched_terms == ["mot special"]
