from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from mailflow.classifier.pipeline import (
    ClassificationPipeline,
    apply_routing_guardrails,
    should_call_ai,
)
from mailflow.classifier.routing_context import ResolvedCounterparty
from mailflow.core.project_digest import build_project_digest
from mailflow.core.project_html_exporter import export_project_correspondence_html
from mailflow.models import (
    AiMailClassification,
    AiMode,
    ArchivedMailRecord,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    RoutingCategory,
    VerifiedRoutingExample,
)
from mailflow.outlook.categories import ARCHIVED_CATEGORY
from mailflow.storage.sqlite_store import SQLiteArchiveStore


class FakeDirectory:
    def __init__(self) -> None:
        self.organizations = {
            "contact@gva.ch": "AIG",
            "sales@metal.test": "Metal Factory",
        }
        self.roles = {
            "contact@gva.ch": InterlocutorType.CLIENT,
            "sales@metal.test": InterlocutorType.FOURNISSEUR,
        }

    def organization_name_for_email(self, email: str) -> str | None:
        return self.organizations.get(email)

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        assert project_number == "2025-4893"
        return self.roles.get(email)


class FakeAiClassifier:
    def __init__(self, responses: list[AiMailClassification]) -> None:
        self.responses = responses
        self.contexts: list[dict[str, Any]] = []

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        del mail, include_body, privacy_mask_phone_numbers
        self.contexts.append(known_context or {})
        return self.responses[len(self.contexts) - 1]


class FailingAiClassifier:
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        del mail, include_body, privacy_mask_phone_numbers, known_context
        raise RuntimeError("OpenAI indisponible")


def ai_result(
    category: Literal["Correspondance", "Demande de prix", "Commande"],
    role: Literal["client", "fournisseur", "inconnu"],
    *,
    company: str | None = None,
    confidence: float = 0.92,
) -> AiMailClassification:
    return AiMailClassification(
        category=category,
        organization_role=role,
        organization_name=company,
        confidence=confidence,
        requires_review=False,
        short_summary="Decision semantique.",
        reason="Le sens global de l'echange determine sa phase commerciale.",
        evidence=["decision confirmee dans le message"],
    )


def mail(
    *,
    entry_id: str = "ENTRY-1",
    direction: Direction = Direction.SENT,
    sender_email: str = "lionel@balzmetal.ch",
    recipients: list[str] | None = None,
    sent_at: datetime | None = None,
    subject: str = "Suivi du projet",
) -> MailMetadata:
    return MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=direction,
        subject=subject,
        sender_name="Lionel",
        sender_email=sender_email,
        recipients=recipients or ["contact@gva.ch", "andre@balzmetal.ch"],
        sent_at=sent_at or datetime(2026, 5, 6, 10, 30),
        body_excerpt="Contenu metier complet sans decision par mot-cle.",
    )


def create_project(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)


def test_ai_is_the_only_classifier_when_enabled() -> None:
    assert should_call_ai(ai_mode=AiMode.ALL)
    assert should_call_ai(ai_mode=AiMode.AMBIGUOUS_ONLY)
    assert not should_call_ai(ai_mode=AiMode.DISABLED)


def test_known_client_role_overrides_ai_and_routes_to_correspondence(tmp_path: Path) -> None:
    create_project(tmp_path)
    ai = FakeAiClassifier([ai_result("Commande", "fournisseur")])
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=ai,
        organization_directory=FakeDirectory(),
    )

    row = pipeline.preview_one(mail())

    assert row.decision.mail_type == MailType.CORRESPONDANCE_GENERALE
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance"
    assert row.classification.ai is not None
    assert row.classification.ai.category == "Correspondance"


def test_supplier_is_routed_to_company_subfolder(tmp_path: Path) -> None:
    create_project(tmp_path)
    ai = FakeAiClassifier([ai_result("Demande de prix", "fournisseur")])
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=ai,
        organization_directory=FakeDirectory(),
    )
    supplier_mail = mail(
        direction=Direction.RECEIVED,
        sender_email="sales@metal.test",
        recipients=["lionel@balzmetal.ch"],
    )

    row = pipeline.preview([supplier_mail])[0]

    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.target_relative_folder == (
        "Fournisseurs/Demande de prix/Metal Factory"
    )


def test_supplier_correspondence_result_is_sent_to_review(tmp_path: Path) -> None:
    create_project(tmp_path)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=FakeAiClassifier([ai_result("Correspondance", "client")]),
        organization_directory=FakeDirectory(),
    )

    row = pipeline.preview_one(
        mail(
            direction=Direction.RECEIVED,
            sender_email="sales@metal.test",
            recipients=["lionel@balzmetal.ch"],
        )
    )

    assert row.action == PreviewAction.REVIEW
    assert row.decision.target_relative_folder == "A verifier"


def test_unknown_directory_role_is_never_silently_archived(tmp_path: Path) -> None:
    create_project(tmp_path)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=FakeAiClassifier([ai_result("Correspondance", "client", company="GVA")]),
        organization_directory=FakeDirectory(),
    )

    row = pipeline.preview_one(mail(recipients=["unknown@example.test"]))

    assert row.action == PreviewAction.REVIEW
    assert row.decision.interlocutor == InterlocutorType.INCONNU
    assert "annuaire" in row.classification.ai.reason if row.classification.ai else False


def test_disabled_or_failed_ai_has_no_keyword_fallback(tmp_path: Path) -> None:
    create_project(tmp_path)
    explicit_subject = "COMMANDE FERME ET CONFIRMEE"
    disabled = ClassificationPipeline(projects_root=tmp_path, ai_mode=AiMode.DISABLED)
    failing = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=FailingAiClassifier(),
    )

    disabled_row = disabled.preview_one(mail(subject=explicit_subject))
    failing_row = failing.preview_one(mail(subject=explicit_subject))

    assert disabled_row.action == PreviewAction.REVIEW
    assert failing_row.action == PreviewAction.REVIEW
    assert disabled_row.classification.rule.matched_rules == []
    assert failing_row.classification.rule.matched_terms == []
    assert disabled_row.classification.ai_error is None
    assert failing_row.classification.ai_error is not None
    assert "Réglages" in failing_row.decision.reason


def test_category_only_archive_keeps_classification_for_html(tmp_path: Path) -> None:
    create_project(tmp_path)
    classifier = FakeAiClassifier([ai_result("Correspondance", "client")])
    pipeline = ClassificationPipeline(
        projects_root=tmp_path, ai_classifier=classifier, organization_directory=FakeDirectory(),
    )
    archived_mail = mail().model_copy(update={"categories": [ARCHIVED_CATEGORY]})

    row = pipeline.preview([archived_mail])[0]

    assert len(classifier.contexts) == 1
    assert row.action == PreviewAction.ARCHIVED
    assert row.decision.duplicate_status == "already_archived"
    assert not row.decision.requires_review
    assert row.decision.mail_type == MailType.CORRESPONDANCE_GENERALE
    assert row.decision.target_relative_folder == "Correspondance/AIG"


@pytest.mark.parametrize("has_outlook_category", [False, True])
def test_archived_sqlite_mail_preserves_original_classification_without_ai(
    tmp_path: Path, has_outlook_category: bool,
) -> None:
    create_project(tmp_path)
    classifier = FakeAiClassifier([])
    archived_mail = mail().model_copy(update={
        "categories": [ARCHIVED_CATEGORY] if has_outlook_category else [],
    })
    store = SQLiteArchiveStore(tmp_path / "archive.sqlite")
    record = ArchivedMailRecord(
        outlook_entry_id=archived_mail.entry_id,
        project_number=archived_mail.project_number,
        subject=archived_mail.subject,
        sent_at=archived_mail.sent_at,
        msg_path=tmp_path / "original" / "mail.msg",
        target_folder="Fournisseurs/Commande/Original Supplier",
        classification=MailType.COMMANDE,
        confidence=0.93,
        archived_at=datetime(2026, 5, 7),
    )
    store.record_archived(record)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path, ai_classifier=classifier, archive_state=store,
        organization_directory=FakeDirectory(),
    )

    row = pipeline.preview([archived_mail])[0]

    assert classifier.contexts == []
    assert row.action == PreviewAction.ARCHIVED
    assert row.classification.ai is None
    assert row.decision.mail_type == record.classification
    assert row.decision.target_relative_folder == record.target_folder
    assert row.decision.target_path == record.msg_path.parent
    assert row.decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert row.decision.confidence == record.confidence
    assert not row.decision.requires_review
    exported = export_project_correspondence_html([row], {}, tmp_path)[0]
    rendered = exported.html_path.read_text(encoding="utf-8")
    assert 'data-folder="Fournisseurs/Commande/Original Supplier"' in rendered
    assert build_project_digest([row]).order_points


def test_archived_classification_contributes_to_later_ai_context(tmp_path: Path) -> None:
    create_project(tmp_path)
    archived_mail = mail()
    store = SQLiteArchiveStore(tmp_path / "archive.sqlite")
    store.record_archived(ArchivedMailRecord(
        outlook_entry_id=archived_mail.entry_id,
        project_number=archived_mail.project_number,
        sent_at=archived_mail.sent_at,
        msg_path=tmp_path / "original" / "mail.msg",
        target_folder="Correspondance/AIG",
        classification=MailType.CORRESPONDANCE_GENERALE,
        confidence=0.95,
        archived_at=datetime(2026, 5, 7),
    ))
    classifier = FakeAiClassifier([ai_result("Correspondance", "client")])
    pipeline = ClassificationPipeline(
        projects_root=tmp_path, ai_classifier=classifier, archive_state=store,
        organization_directory=FakeDirectory(),
    )

    pipeline.preview([
        archived_mail,
        mail(entry_id="NEW", sent_at=archived_mail.sent_at + timedelta(hours=1)),
    ])

    assert len(classifier.contexts) == 1
    history = classifier.contexts[0]["recent_company_history"]
    assert len(history) == 1
    assert history[0]["category"] == "Correspondance"
    assert history[0]["subject"] == archived_mail.subject


def test_ai_failure_does_not_expose_response_or_credential(tmp_path: Path) -> None:
    class SensitiveFailure(FailingAiClassifier):
        def classify(self, mail: MailMetadata, **kwargs: Any) -> AiMailClassification:
            raise RuntimeError("secret-key-123 private message content")

    create_project(tmp_path)
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=SensitiveFailure())

    row = pipeline.preview_one(mail())

    assert "secret-key-123" not in row.model_dump_json()
    assert "private message content" not in row.model_dump_json()
    assert row.classification.ai_error is not None
    assert row.action == PreviewAction.REVIEW


def test_first_external_recipient_and_history_are_sent_to_ai(tmp_path: Path) -> None:
    create_project(tmp_path)
    start = datetime(2026, 5, 6, 9, 0)
    classifier = FakeAiClassifier(
        [
            ai_result("Correspondance", "client"),
            ai_result("Correspondance", "client"),
        ]
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=classifier,
        organization_directory=FakeDirectory(),
    )
    later = mail(entry_id="LATER", sent_at=start + timedelta(hours=1))
    earlier = mail(entry_id="EARLIER", sent_at=start)

    rows = pipeline.preview([later, earlier])

    assert [row.mail.entry_id for row in rows] == ["LATER", "EARLIER"]
    first_context = classifier.contexts[0]
    second_context = classifier.contexts[1]
    assert first_context["counterparty"]["primary_email"] == "contact@gva.ch"
    assert first_context["counterparty"]["organization_name"] == "AIG"
    assert second_context["recent_company_history"][0]["subject"] == earlier.subject


def test_verified_manual_examples_are_included_in_context(tmp_path: Path) -> None:
    create_project(tmp_path)
    classifier = FakeAiClassifier([ai_result("Correspondance", "client")])
    example = VerifiedRoutingExample(
        project_number="2025-4893",
        subject="Validation manuelle precedente",
        organization_name="AIG",
        organization_role=InterlocutorType.CLIENT,
        category=RoutingCategory.CORRESPONDANCE,
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=classifier,
        organization_directory=FakeDirectory(),
        verified_examples=[example],
    )

    pipeline.preview_one(mail())

    examples = classifier.contexts[0]["verified_manual_examples"]
    assert examples[0]["subject"] == "Validation manuelle precedente"


def test_preview_reports_progress_for_every_ai_decision(tmp_path: Path) -> None:
    create_project(tmp_path)
    classifier = FakeAiClassifier(
        [ai_result("Correspondance", "client"), ai_result("Correspondance", "client")]
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=classifier,
        organization_directory=FakeDirectory(),
    )
    progress: list[tuple[int, int]] = []

    pipeline.preview(
        [mail(entry_id="1"), mail(entry_id="2")],
        progress_callback=lambda index, total: progress.append((index, total)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_guardrails_use_configured_confidence_threshold() -> None:
    supplier = ResolvedCounterparty(
        email="sales@metal.test",
        organization_name="Metal Factory",
        role=InterlocutorType.FOURNISSEUR,
        organization_locked=True,
        source="annuaire",
    )
    result = ai_result("Commande", "fournisseur", company="Metal Factory", confidence=0.85)

    assert not apply_routing_guardrails(result, supplier).requires_review
    assert apply_routing_guardrails(
        result, supplier, confidence_threshold=0.90,
    ).requires_review
