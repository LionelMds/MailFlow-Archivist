from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.classifier.decision_engine import decide_archive, destination_for
from mailflow.core.mail_file_plan import planned_msg_path
from mailflow.models import (
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    RuleClassification,
)
from mailflow.outlook.categories import ARCHIVED_CATEGORY


class InMemoryArchiveState:
    def __init__(self, archived_ids: set[str]) -> None:
        self.archived_ids = archived_ids

    def is_archived(self, outlook_entry_id: str) -> bool:
        return outlook_entry_id in self.archived_ids


def sample_mail(*, categories: list[str] | None = None) -> MailMetadata:
    return MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont SA",
        sender_email="sales@dupont.test",
        recipients=["lionel@balzmetal.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        categories=categories or [],
    )


def confident_rule() -> RuleClassification:
    return RuleClassification(
        suggested_type=MailType.DEVIS,
        suggested_interlocutor=InterlocutorType.FOURNISSEUR,
        likely_archive=True,
        confidence=0.9,
        matched_rules=["devis"],
    )


def test_destination_mapping_for_supplier_quote() -> None:
    assert destination_for(MailType.DEVIS, InterlocutorType.FOURNISSEUR) == (
        "Fournisseurs/Demande de prix"
    )


def test_decision_blocks_missing_project_folder(tmp_path: Path) -> None:
    decision = decide_archive(sample_mail(), projects_root=tmp_path, rule=confident_rule())

    assert decision.archive is False
    assert decision.requires_review is True
    assert "Dossier projet local absent" in decision.reason


def test_decision_archives_confident_rule_when_project_exists(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)

    decision = decide_archive(sample_mail(), projects_root=tmp_path, rule=confident_rule())

    assert decision.archive is True
    assert decision.requires_review is False
    assert decision.target_path == (
        tmp_path / "2025" / "2025-4893" / "Fournisseurs/Demande de prix"
    )


def test_decision_skips_outlook_category_already_archived(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)

    decision = decide_archive(
        sample_mail(categories=[ARCHIVED_CATEGORY]),
        projects_root=tmp_path,
        rule=confident_rule(),
    )

    assert decision.archive is False
    assert decision.duplicate_status == "already_archived"


def test_decision_skips_sqlite_already_archived(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)

    decision = decide_archive(
        sample_mail(),
        projects_root=tmp_path,
        rule=confident_rule(),
        archive_state=InMemoryArchiveState({"ENTRY-1"}),
    )

    assert decision.archive is False
    assert decision.duplicate_status == "already_archived"


def test_decision_requires_review_for_low_confidence(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    rule = RuleClassification(
        suggested_type=MailType.CORRESPONDANCE_GENERALE,
        suggested_interlocutor=InterlocutorType.INCONNU,
        likely_archive=True,
        confidence=0.5,
        matched_rules=[],
    )

    decision = decide_archive(sample_mail(), projects_root=tmp_path, rule=rule)

    assert decision.archive is False
    assert decision.requires_review is True


def test_decision_detects_candidate_file_conflict(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    existing = tmp_path / "existing.msg"
    existing.write_text("already here", encoding="utf-8")

    decision = decide_archive(
        sample_mail(),
        projects_root=tmp_path,
        rule=confident_rule(),
        candidate_msg_path=existing,
    )

    assert decision.archive is False
    assert decision.duplicate_status == "same_file_exists"


def test_decision_detects_planned_msg_file_conflict(tmp_path: Path) -> None:
    project_path = tmp_path / "2025" / "2025-4893"
    target_path = project_path / "Fournisseurs/Demande de prix"
    target_path.mkdir(parents=True)
    mail = sample_mail().model_copy(update={"archive_order": 1})
    planned_msg_path(mail, target_path).write_text("already here", encoding="utf-8")

    decision = decide_archive(mail, projects_root=tmp_path, rule=confident_rule())

    assert decision.archive is False
    assert decision.requires_review is True
    assert decision.duplicate_status == "same_file_exists"
