from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from mailflow.classifier.pipeline import ClassificationPipeline, should_call_ai
from mailflow.models import (
    AiMailClassification,
    AiMode,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    RoutingCategory,
    VerifiedRoutingExample,
)


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
