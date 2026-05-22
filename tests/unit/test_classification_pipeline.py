from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.classifier.pipeline import ClassificationPipeline, should_call_ai
from mailflow.core.manual_review import LearnedClassificationRule, LearnedMisleadingTerm
from mailflow.models import (
    AiMailClassification,
    AiMode,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    RuleClassification,
)


class FakeAiClassifier:
    def __init__(self) -> None:
        self.calls: list[MailMetadata] = []

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        self.calls.append(mail)
        return AiMailClassification(
            archive=True,
            usefulness="normal",
            mail_type="correspondance_generale",
            interlocutor="client",
            target_folder="Correspondance",
            confidence=0.85,
            short_summary="Question projet.",
            reason="Mail utile mais ambigu, classe par IA.",
        )


class FailingAiClassifier:
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        raise RuntimeError("OpenAI indisponible")


class InternalAiClassifier:
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        return AiMailClassification(
            archive=True,
            usefulness="normal",
            mail_type="plan",
            interlocutor="interne",
            target_folder="Correspondance",
            confidence=0.84,
            short_summary="Plan actualise.",
            reason="Signature et copie internes.",
        )


class SupplierAiClassifier:
    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, str] | None = None,
    ) -> AiMailClassification:
        return AiMailClassification(
            archive=True,
            usefulness="important",
            mail_type="commande",
            interlocutor="fournisseur",
            target_folder="Fournisseurs/Commande",
            confidence=0.92,
            short_summary="Commande detectee.",
            reason="Le mot commande a ete detecte.",
        )


def sample_mail(*, subject: str, body: str = "") -> MailMetadata:
    return MailMetadata(
        entry_id=f"ENTRY-{subject}",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject=subject,
        sender_name="Dupont SA",
        sender_email="sales@dupont.test",
        recipients=["lionel@balzmetal.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        body_excerpt=body,
    )


def test_should_call_ai_for_ambiguous_rule() -> None:
    rule = RuleClassification(
        suggested_type=None,
        suggested_interlocutor=None,
        likely_archive=None,
        confidence=0,
        matched_rules=[],
    )

    assert should_call_ai(rule, ai_mode=AiMode.AMBIGUOUS_ONLY, threshold=0.8)


def test_should_not_call_ai_for_confident_rule_in_ambiguous_mode() -> None:
    rule = RuleClassification(
        suggested_type=MailType.DEVIS,
        suggested_interlocutor=None,
        likely_archive=True,
        confidence=0.9,
        matched_rules=["devis"],
    )

    assert not should_call_ai(rule, ai_mode=AiMode.AMBIGUOUS_ONLY, threshold=0.8)


def test_pipeline_uses_rules_without_ai_for_confident_mail(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    ai = FakeAiClassifier()
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=ai)

    row = pipeline.preview_one(sample_mail(subject="Invoice", body="Please find attached invoice."))

    assert row.classification.ai is None
    assert ai.calls == []
    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.mail_type == MailType.FACTURE


def test_pipeline_preview_applies_company_hierarchy(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_mode=AiMode.DISABLED)

    rows = pipeline.preview([sample_mail(subject="Offerte", body="Voici notre offre.")])

    assert rows[0].decision.target_relative_folder == "Fournisseurs/Demande de prix/Dupont SA"


def test_pipeline_calls_ai_for_ambiguous_mail(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    ai = FakeAiClassifier()
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=ai)

    row = pipeline.preview_one(sample_mail(subject="Question", body="Pouvez-vous regarder ?"))

    assert len(ai.calls) == 1
    assert row.classification.ai is not None
    assert row.decision.target_relative_folder == "Correspondance"
    assert row.action == PreviewAction.ARCHIVE


def test_pipeline_corrects_ai_internal_when_sent_mail_primary_recipient_is_external(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    mail = MailMetadata(
        entry_id="ENTRY-SENT-PLAN",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.SENT,
        subject="RE: Approbation du plan de la cloison",
        sender_name="Lionel",
        sender_email="lionel@balzmetal.ch",
        recipients=["blaise.riva@gva.ch", "andre@balzmetal.ch"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=["2025-4788-ENS-100_B.pdf"],
        body_excerpt="Veuillez trouver ci-joint le plan actualise.",
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=InternalAiClassifier(),
    )

    row = pipeline.preview([mail])[0]

    assert row.classification.ai is not None
    assert row.classification.ai.interlocutor == "client"
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance/GVA"
    assert row.action == PreviewAction.ARCHIVE


def test_pipeline_corrects_received_gva_mail_to_client_even_when_ai_says_external(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    mail = MailMetadata(
        entry_id="ENTRY-RECEIVED-GVA",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="TR: Approbation du plan de la cloison",
        sender_name="RIVA Blaise",
        sender_email="",
        recipients=["lionel@balzmetal.ch", "andre@balzmetal.ch"],
        sent_at=datetime(2026, 4, 7, 15, 10),
        body_excerpt=(
            "Geneve Aeroport\n"
            "Blaise RIVA - Technicien architecte\n"
            "blaise.riva@gva.ch"
        ),
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=InternalAiClassifier(),
    )

    row = pipeline.preview([mail])[0]

    assert row.classification.ai is not None
    assert row.classification.ai.interlocutor == "client"
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance/GVA"
    assert row.action == PreviewAction.ARCHIVE


def test_pipeline_known_client_domain_overrides_supplier_rule_keywords(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    mail = MailMetadata(
        entry_id="ENTRY-SENT-GVA-COMMANDE",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.SENT,
        subject="TR: Commande et approbation du plan",
        sender_name="Lionel",
        sender_email="lionel@balzmetal.ch",
        recipients=["damien.pasquier@gva.ch"],
        sent_at=datetime(2026, 4, 23, 13, 46),
        attachment_names=["2025-4788-ENS-100_B.pdf"],
        body_excerpt="Veuillez trouver ci-joint les plans pour approbation.",
    )
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_mode=AiMode.DISABLED)

    row = pipeline.preview([mail])[0]

    assert row.classification.rule.suggested_interlocutor == InterlocutorType.CLIENT
    assert row.decision.mail_type == MailType.COMMANDE
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance/GVA"
    assert row.action == PreviewAction.ARCHIVE


def test_pipeline_known_client_domain_overrides_ai_supplier_decision(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    mail = MailMetadata(
        entry_id="ENTRY-SENT-GVA-AI-SUPPLIER",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.SENT,
        subject="TR: Commande et approbation du plan",
        sender_name="Lionel",
        sender_email="lionel@balzmetal.ch",
        recipients=["damien.pasquier@gva.ch"],
        sent_at=datetime(2026, 4, 23, 13, 46),
        attachment_names=["2025-4788-ENS-100_B.pdf"],
        body_excerpt="Veuillez trouver ci-joint les plans pour approbation.",
    )
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.ALL,
        ai_classifier=SupplierAiClassifier(),
    )

    row = pipeline.preview([mail])[0]

    assert row.classification.ai is not None
    assert row.classification.ai.interlocutor == "client"
    assert row.classification.ai.target_folder == "Correspondance"
    assert row.decision.interlocutor == InterlocutorType.CLIENT
    assert row.decision.target_relative_folder == "Correspondance/GVA"
    assert row.action == PreviewAction.ARCHIVE


def test_pipeline_disabled_ai_keeps_ambiguous_mail_for_review(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_mode=AiMode.DISABLED)

    row = pipeline.preview_one(sample_mail(subject="Question", body="Pouvez-vous regarder ?"))

    assert row.classification.ai is None
    assert row.action == PreviewAction.REVIEW


def test_pipeline_falls_back_to_rules_when_ai_fails(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_classifier=FailingAiClassifier(),
    )

    row = pipeline.preview_one(sample_mail(subject="Question", body="Pouvez-vous regarder ?"))

    assert row.classification.ai is None
    assert row.action == PreviewAction.REVIEW


def test_pipeline_uses_learned_terms_before_regular_rules(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.DISABLED,
        learned_rules=[
            LearnedClassificationRule(
                term="mot appris",
                mail_type=MailType.ADMINISTRATIF,
                interlocutor=InterlocutorType.CLIENT,
                target_relative_folder="Correspondance",
            )
        ],
    )

    row = pipeline.preview_one(sample_mail(subject="Mot appris", body=""))

    assert row.decision.mail_type == MailType.ADMINISTRATIF
    assert row.classification.rule.matched_rules == ["apprentissage:mot appris"]


def test_pipeline_ignores_learned_misleading_terms(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    pipeline = ClassificationPipeline(
        projects_root=tmp_path,
        ai_mode=AiMode.DISABLED,
        misleading_terms=[LearnedMisleadingTerm(term="offre")],
    )

    row = pipeline.preview_one(sample_mail(subject="Offre spéciale", body="Annonce"))

    assert row.decision.mail_type == MailType.A_VERIFIER
    assert row.classification.rule.matched_terms == []
