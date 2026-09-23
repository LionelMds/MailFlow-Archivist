from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mailflow.core.evaluation import (
    EvaluationCase,
    evaluate_case,
    format_summary,
    load_reference_cases,
    result_to_json,
    summarize,
)
from mailflow.models import (
    AiMailClassification,
    ArchivedMailRecord,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualLearningSignal,
    RoutingCategory,
)
from mailflow.storage.learning_store import SQLiteLearningStore
from mailflow.storage.sqlite_store import SQLiteArchiveStore

Category = Literal["Correspondance", "Demande de prix", "Commande"]


def archive(store: SQLiteArchiveStore, entry_id: str, folder: str) -> None:
    store.record_archived(ArchivedMailRecord(
        outlook_entry_id=entry_id,
        project_number="2025-4893",
        subject="Sujet confidentiel",
        sender="x@acme.test",
        sent_at=datetime(2026, 5, 6, 10, 30),
        msg_path=Path("C:/archive") / f"{entry_id}.msg",
        target_folder=folder,
        classification=MailType.COMMANDE,
        confidence=0.9,
        archived_at=datetime(2026, 5, 7, tzinfo=UTC),
    ))


def correction(
    entry_id: str,
    mail_type: MailType,
    role: InterlocutorType,
    folder: str = "A verifier",
) -> ManualLearningSignal:
    return ManualLearningSignal(
        mail_id=entry_id,
        project_number="2025-4893",
        subject="Sujet confidentiel",
        selected_mail_type=mail_type,
        selected_interlocutor=role,
        selected_target_folder=folder,
        learning_term=None,
        misleading_term=None,
        manual_required=False,
        created_at=datetime(2026, 5, 6, 10, 30),
        organization_name="Acme",
        primary_email="x@acme.test",
    )


def test_reference_cases_prefer_latest_correction_over_archive(tmp_path: Path) -> None:
    db = tmp_path / "mailflow.sqlite"
    archives = SQLiteArchiveStore(db)
    archive(archives, "A1", "Correspondance/Acme")
    archive(archives, "A2", "Fournisseurs/Demande de prix/Steel")
    archive(archives, "A3", "Plans")  # legacy folder: no reliable category
    archive(archives, "A4", "Fournisseurs/Commande/Steel")
    learning = SQLiteLearningStore(db)
    learning.record(correction("A2", MailType.COMMANDE, InterlocutorType.FOURNISSEUR))
    learning.record(correction("C1", MailType.DEVIS, InterlocutorType.FOURNISSEUR))
    learning.record(correction("C2", MailType.COMMANDE, InterlocutorType.FOURNISSEUR))
    learning.record(correction("C2", MailType.A_VERIFIER, InterlocutorType.INCONNU))
    learning.record(correction("A4", MailType.A_VERIFIER, InterlocutorType.INCONNU))

    cases = {case.entry_id: case for case in load_reference_cases(db)}

    assert set(cases) == {"A1", "A2", "C1"}
    assert (cases["A1"].expected_category, cases["A1"].source) == (
        RoutingCategory.CORRESPONDANCE, "archive",
    )
    assert (cases["A2"].expected_category, cases["A2"].source) == (
        RoutingCategory.COMMANDE, "correction",
    )
    assert cases["C1"].expected_category == RoutingCategory.DEMANDE_DE_PRIX


def test_references_contradicting_current_rules_are_left_out(tmp_path: Path) -> None:
    db = tmp_path / "mailflow.sqlite"
    archives = SQLiteArchiveStore(db)
    archive(archives, "TYPE_VS_FOLDER", "Fournisseurs/Demande de prix/Steel")
    learning = SQLiteLearningStore(db)
    # Older rules allowed a supplier in Correspondance.
    learning.record(correction(
        "OLD_RULE", MailType.CORRESPONDANCE_GENERALE, InterlocutorType.FOURNISSEUR,
        "Correspondance/Steel",
    ))
    learning.record(correction("CLIENT_ORDER", MailType.COMMANDE, InterlocutorType.CLIENT))
    # Type says order, folder says price request: keep where the mail was archived.
    learning.record(correction(
        "TYPE_VS_FOLDER", MailType.COMMANDE, InterlocutorType.FOURNISSEUR,
        "Fournisseurs/Demande de prix/Steel",
    ))

    cases = {case.entry_id: case for case in load_reference_cases(db)}

    assert set(cases) == {"TYPE_VS_FOLDER"}
    assert cases["TYPE_VS_FOLDER"].source == "archive"
    assert cases["TYPE_VS_FOLDER"].expected_category == RoutingCategory.DEMANDE_DE_PRIX


def test_reference_contradicting_directory_role_is_not_scored() -> None:
    classifier = Classifier({})
    client_case = EvaluationCase(
        entry_id="X",
        project_number="2025-4893",
        expected_category=RoutingCategory.CORRESPONDANCE,
        expected_role=InterlocutorType.CLIENT,
        source="archive",
    )

    result = evaluate_case(
        client_case, fetch_mail=mail, classifier=classifier, directory=Directory(),
    )

    assert result.inconsistent_reference
    assert classifier.contexts == []
    summary = summarize([result])
    assert (summary.answered, summary.inconsistent_references) == (0, 1)


def test_missing_database_has_no_reference(tmp_path: Path) -> None:
    assert load_reference_cases(tmp_path / "absent.sqlite") == []
    assert not (tmp_path / "absent.sqlite").exists()


class Directory:
    def organization_name_for_email(self, email: str) -> str | None:
        return "Steel" if email.endswith("@steel.test") else None

    def interlocutor_for_email(self, project_number: str, email: str) -> InterlocutorType | None:
        return InterlocutorType.FOURNISSEUR if email.endswith("@steel.test") else None


class Classifier:
    def __init__(self, answers: dict[str, tuple[Category, float]]) -> None:
        self.answers = answers
        self.contexts: list[dict[str, Any]] = []
        self.last_usage: tuple[int, int] | None = None

    def classify(
        self,
        mail: MailMetadata,
        *,
        include_body: bool = True,
        privacy_mask_phone_numbers: bool = False,
        known_context: dict[str, Any] | None = None,
    ) -> AiMailClassification:
        if mail.entry_id == "BROKEN":
            raise TimeoutError("slow")
        self.contexts.append(known_context or {})
        category, confidence = self.answers[mail.entry_id]
        self.last_usage = (1000, 100)
        return AiMailClassification(
            category=category,
            organization_role="fournisseur",
            organization_name="Steel",
            confidence=confidence,
            requires_review=False,
            short_summary="Resume.",
            reason="Raison.",
            evidence=[],
        )


def mail(entry_id: str) -> MailMetadata:
    return MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="2025-4893",
        direction=Direction.RECEIVED,
        subject="Sujet",
        sender_name="Vente",
        sender_email="vente@steel.test",
        recipients=["atelier@balzmetal.ch"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        attachment_names=[],
        body_excerpt="",
    )


def case(entry_id: str, category: RoutingCategory) -> EvaluationCase:
    return EvaluationCase(
        entry_id=entry_id,
        project_number="2025-4893",
        expected_category=category,
        expected_role=InterlocutorType.FOURNISSEUR,
        source="correction",
    )


def test_evaluation_scores_automatic_errors_separately() -> None:
    classifier = Classifier({
        "OK": ("Commande", 0.95),
        "WRONG": ("Demande de prix", 0.95),
        "UNSURE": ("Commande", 0.5),
    })
    cases = [
        case("OK", RoutingCategory.COMMANDE),
        case("WRONG", RoutingCategory.COMMANDE),
        case("UNSURE", RoutingCategory.COMMANDE),
        case("GONE", RoutingCategory.COMMANDE),
        case("BROKEN", RoutingCategory.COMMANDE),
    ]

    results = [
        evaluate_case(
            item,
            fetch_mail=lambda entry_id: None if entry_id == "GONE" else mail(entry_id),
            classifier=classifier,
            directory=Directory(),
        )
        for item in cases
    ]
    summary = summarize(results)

    assert (summary.answered, summary.correct) == (3, 2)
    assert (summary.missing_in_outlook, summary.errors) == (1, 1)
    assert (summary.automatic_correct, summary.automatic_wrong, summary.sent_to_review) == (
        1, 1, 1,
    )
    assert summary.confusion["Commande"] == {
        "Correspondance": 0, "Demande de prix": 1, "Commande": 2,
    }
    assert (summary.input_tokens, summary.output_tokens) == (3000, 300)
    assert summary.by_source == {"correction": (2, 3)}
    # The model never sees history or verified examples that could leak the answer.
    assert all(
        context["recent_company_history"] == []
        and context["verified_manual_examples"] == []
        for context in classifier.contexts
    )

    text = format_summary(summary, engine="test", price_per_million=(1.0, 10.0))
    assert "66.7% (2/3)" in text
    assert "Classes automatiquement mais FAUX : 1" in text
    assert "Cout estime : 0.0060" in text

    report = result_to_json(results[1])
    assert report["correct"] is False
    assert "Sujet" not in str(report)
