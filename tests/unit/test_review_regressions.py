"""Regressions fixed by the November 2026 project review."""

from __future__ import annotations

import logging
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

import mailflow
from mailflow.classifier.ai_classifier import AiResponseError, _safe_error_message
from mailflow.classifier.decision_engine import decide_archive
from mailflow.classifier.jev_classifier import JevError, _choice_probabilities
from mailflow.classifier.pipeline import (
    MAX_REASON_LENGTH,
    ClassificationPipeline,
    apply_routing_guardrails,
)
from mailflow.classifier.routing_context import ResolvedCounterparty, resolve_counterparty
from mailflow.core.app_controller import AppController, apply_directory_roles_to_rows
from mailflow.core.archive_service import ArchiveService
from mailflow.core.body_cleaner import clean_body
from mailflow.core.contact_directory import is_generic_email_domain
from mailflow.core.correspondence_hierarchy import company_from_mail, company_key_from_mail
from mailflow.core.filenames import build_attachment_filename
from mailflow.core.folder_tree import merge_folder, rename_folder_leaf
from mailflow.core.mailbox_sorting import (
    MailboxAnalysis,
    ProjectSuggestion,
    SortProposal,
    SortStatus,
    missing_project_numbers,
    replan_proposal,
)
from mailflow.core.project_html_exporter import (
    ProjectHtmlExistsError,
    export_project_correspondence_html,
)
from mailflow.core.project_references import ProjectReference, ReferenceSource
from mailflow.core.reporting import preview_row_to_report_record
from mailflow.logging import SecretRedactionFormatter, redact_secrets
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
from mailflow.outlook.client import _split_path
from mailflow.outlook.exporter import ExportResult
from mailflow.outlook.mailbox import MailboxSourceKind
from mailflow.outlook.scanner import OutlookScanner
from mailflow.ui.main_window import project_folder_to_open, review_reminder_due_key

PROJECT = "2025-4893"


def make_mail(**updates: Any) -> MailMetadata:
    values: dict[str, Any] = {
        "entry_id": "ENTRY-1",
        "project_number": PROJECT,
        "outlook_folder": f"Boite de reception/2025/{PROJECT}",
        "direction": Direction.RECEIVED,
        "subject": "Offre",
        "sender_name": "Vente Aciers",
        "sender_email": "vente@aciers.ch",
        "recipients": ["Lionel <lionel@balzmetal.ch>"],
        "sent_at": datetime(2026, 5, 6, 10, 30),
    }
    return MailMetadata(**(values | updates))


def make_answer(category: str, role: str, confidence: float, reason: str = "") -> (
    AiMailClassification
):
    return AiMailClassification.model_validate({
        "category": category, "organization_role": role, "organization_name": None,
        "confidence": confidence, "requires_review": confidence < 0.8,
        "short_summary": "Resume", "reason": reason or "Raison du moteur.", "evidence": [],
    })


def make_row(
    tmp_path: Path,
    mail: MailMetadata,
    *,
    target: str = "A verifier",
    interlocutor: InterlocutorType = InterlocutorType.INCONNU,
    ai: AiMailClassification | None = None,
    action: PreviewAction = PreviewAction.REVIEW,
) -> PreviewRow:
    project_path = tmp_path / "2025" / PROJECT
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(rule=RuleClassification(confidence=0.0), ai=ai),
        decision=ArchiveDecision(
            mail_id=mail.entry_id,
            project_number=PROJECT,
            archive=action == PreviewAction.ARCHIVE,
            requires_review=action == PreviewAction.REVIEW,
            mail_type=MailType.COMMANDE,
            interlocutor=interlocutor,
            target_relative_folder=target,
            target_path=(
                project_path
                if target == "A verifier"
                else project_path.joinpath(*target.split("/"))
            ),
            confidence=0.9,
            duplicate_status="none",
            reason="ok",
        ),
        action=action,
    )


class Directory:
    """Directory with one supplier (aciers.ch) and one client (archi.ch)."""

    names = {"aciers.ch": "Aciers SA", "archi.ch": "Archi Sarl"}
    roles = {"aciers.ch": InterlocutorType.FOURNISSEUR, "archi.ch": InterlocutorType.CLIENT}

    def organization_name_for_email(self, email: str) -> str | None:
        return self.names.get(email.rsplit("@", 1)[-1])

    def interlocutor_for_email(self, project_number: str, email: str) -> InterlocutorType | None:
        return self.roles.get(email.rsplit("@", 1)[-1])


# --- company folder and counterparty -------------------------------------------------


def test_sent_mail_folder_follows_the_first_external_recipient() -> None:
    recipients = ["vente@aciers.ch", "Architecte Associes Sarl <archi@archi.ch>"]

    company = company_from_mail(
        Direction.SENT, "Lionel", "lionel@balzmetal.ch", recipients,
        organization_directory=Directory(),
    )

    # The role comes from the first external recipient: so does the folder name.
    assert company == "Aciers SA"


def test_internal_microsoft_address_is_not_taken_for_a_company() -> None:
    recipients = ["andre@balzmetalsa.onmicrosoft.com", "vente@aciers.ch"]

    assert company_from_mail(Direction.SENT, "", "lionel@balzmetal.ch", recipients) == "Aciers"
    assert company_key_from_mail(Direction.SENT, "", "lionel@balzmetal.ch", recipients) == (
        "aciers"
    )
    # A look-alike domain stays external.
    assert company_from_mail(Direction.SENT, "", "", ["x@notbalzmetal.ch"]) == "Notbalzmetal"


def test_mail_of_a_colleague_read_from_a_copy_is_about_its_recipient() -> None:
    mail = make_mail(
        sender_name="Andre", sender_email="andre@balzmetal.ch",
        recipients=["vente@aciers.ch", "lionel@balzmetal.ch"],
    )

    counterparty = resolve_counterparty(mail, Directory())

    assert counterparty.email == "vente@aciers.ch"
    assert counterparty.role == InterlocutorType.FOURNISSEUR
    assert company_from_mail(
        mail.direction, mail.sender_name, mail.sender_email, mail.recipients,
        organization_directory=Directory(),
    ) == "Aciers SA"


# --- guardrails and decision ---------------------------------------------------------


def client_counterparty(*, locked: bool = True) -> ResolvedCounterparty:
    return ResolvedCounterparty(
        email="archi@archi.ch", organization_name="Archi Sarl",
        role=InterlocutorType.CLIENT, organization_locked=locked, source="sender",
    )


def test_directory_client_is_routed_without_review_whatever_the_engine_confidence() -> None:
    hesitant = make_answer("Demande de prix", "inconnu", 0.55)

    guarded = apply_routing_guardrails(hesitant, client_counterparty())

    assert guarded.category == "Correspondance"
    assert guarded.organization_role == "client"
    assert guarded.confidence == 1.0
    assert guarded.requires_review is False
    assert guarded.target_folder == "Correspondance"


def test_guardrail_notes_survive_a_long_engine_reason() -> None:
    supplier = ResolvedCounterparty(
        email="vente@aciers.ch", organization_name="Aciers SA",
        role=InterlocutorType.FOURNISSEUR, organization_locked=True, source="sender",
    )
    answer = make_answer("Correspondance", "fournisseur", 0.95, reason="x" * 198)

    guarded = apply_routing_guardrails(answer, supplier)

    assert guarded.requires_review is True
    assert "Un fournisseur doit etre classe en demande de prix ou commande." in guarded.reason
    assert len(guarded.reason) <= MAX_REASON_LENGTH


def test_openai_local_error_message_is_shown_like_those_of_the_other_engines(
    tmp_path: Path,
) -> None:
    class Refusing:
        def classify(self, *_args: Any, **_kwargs: Any) -> AiMailClassification:
            raise AiResponseError("Le modele a refuse cette classification.")

    pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=Refusing())

    row = pipeline.preview_one(make_mail())

    assert row.classification.ai_error == "Le modele a refuse cette classification."


def test_review_reason_says_when_no_engine_answered(tmp_path: Path) -> None:
    (tmp_path / "2025" / PROJECT).mkdir(parents=True)

    decision = decide_archive(
        make_mail(), projects_root=tmp_path, rule=RuleClassification(confidence=0.0),
    )

    assert decision.requires_review is True
    assert "IA indisponible ou desactivee" in decision.reason


def test_jev_answer_with_a_malformed_choice_is_a_jev_error() -> None:
    options = {"a", "b"}
    data = {"answers": {"q": {
        "type": "choice", "choice": ["a"], "probabilities": {"a": 0.6, "b": 0.4},
    }}}

    with pytest.raises(JevError):
        _choice_probabilities(data, "q", options)


def test_openai_connection_error_never_quotes_the_server_text() -> None:
    class Unauthorized(Exception):
        status_code = 401

    message = _safe_error_message(
        Unauthorized("Incorrect API key provided: sk-proj-abc***xyz"), secret="other",
    )

    assert "sk-proj" not in message
    assert "clé refusée" in message


# --- directory roles -----------------------------------------------------------------


def test_client_role_saved_in_directory_routes_its_mails_without_new_call(
    tmp_path: Path,
) -> None:
    (tmp_path / "2025" / PROJECT).mkdir(parents=True)
    client_mail = make_mail(sender_name="Archi", sender_email="archi@archi.ch")
    supplier_mail = make_mail(entry_id="ENTRY-2")
    rows = [
        make_row(tmp_path, client_mail, ai=make_answer("Demande de prix", "inconnu", 0.6)),
        make_row(tmp_path, supplier_mail, ai=make_answer("Commande", "inconnu", 0.93)),
    ]

    updated = apply_directory_roles_to_rows(rows, cast(Any, Directory()), tmp_path)

    assert updated[0].decision.target_relative_folder == "Correspondance/Archi Sarl"
    assert updated[0].action == PreviewAction.ARCHIVE
    # The supplier answer was given while the role was unknown: it stays in review.
    assert updated[1].decision.target_relative_folder == "Fournisseurs/Commande/Aciers SA"
    assert updated[1].action == PreviewAction.REVIEW


def test_no_role_is_suggested_for_a_company_whose_role_was_chosen_by_hand(
    tmp_path: Path,
) -> None:
    from mailflow.models import RoleEstimate
    from mailflow.storage.directory_store import SQLiteDirectoryStore

    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.add_organization(
        "Bureau Ingenieurs", domain="bureau.ch", role=InterlocutorType.INTERVENANT_EXTERNE,
    )
    store.add_organization("Nouveau SA", domain="nouveau.ch")
    estimate = RoleEstimate(probabilities={"client": 0.95, "fournisseur": 0.05})
    controller = AppController(
        scan_service=cast(Any, None), preview_pipeline=cast(Any, None),
        projects_root=tmp_path, report_dir=tmp_path, directory_store=store,
    )
    controller.preview_rows = [
        make_row(tmp_path, make_mail(entry_id=entry_id, sender_email=sender)).model_copy(
            update={"classification": ClassificationResult(
                rule=RuleClassification(confidence=0.0), role_estimate=estimate,
            )}
        )
        for entry_id, sender in (("1", "a@bureau.ch"), ("2", "b@nouveau.ch"))
    ]

    suggestions = controller.directory_role_suggestions()

    assert list(suggestions) == [store.organization_id_for_email("b@nouveau.ch")]


# --- files ---------------------------------------------------------------------------


def test_long_attachment_name_keeps_its_extension() -> None:
    original = (
        "2026-5245_Offre_Balz_Metal_SA_Construction_metallique_Facade_Est_Rev_B_"
        "2026-03-14_signee.pdf"
    )

    name = build_attachment_filename("3-R-Offre", original)

    assert name.endswith(".pdf")
    assert len(name) <= 180


def test_attachments_of_a_mail_with_a_long_subject_keep_their_own_names() -> None:
    stem = "12-R-" + "sujet tres long " * 11

    plan_a = build_attachment_filename(stem.strip(), "plan A.pdf")
    plan_b = build_attachment_filename(stem.strip(), "plan B.pdf")

    assert plan_a.endswith(" - plan A.pdf")
    assert plan_b.endswith(" - plan B.pdf")
    assert max(len(plan_a), len(plan_b)) <= 180


def test_a_dot_in_a_name_is_not_taken_for_an_extension() -> None:
    assert build_attachment_filename("1-R-Mail", "Offre no. 12 du client") == (
        "1-R-Mail - Offre no. 12 du client"
    )


def test_journal_failure_takes_back_the_files_just_published(tmp_path: Path) -> None:
    class Exporter:
        def export_mail(self, _item: Any, _metadata: Any, decision: ArchiveDecision) -> (
            ExportResult
        ):
            msg_path = decision.target_path / "1-R-Offre.msg"
            attachment = decision.target_path / "1-R-Offre - plan.pdf"
            msg_path.write_text("msg", encoding="utf-8")
            attachment.write_text("pdf", encoding="utf-8")
            return ExportResult(msg_path=msg_path, attachment_paths=[attachment])

    class LockedStore:
        def record_archived(self, _record: Any) -> bool:
            raise OSError("database is locked")

    row = make_row(tmp_path, make_mail(), target="Correspondance", action=PreviewAction.ARCHIVE)
    row.decision.target_path.mkdir(parents=True)
    kept = row.decision.target_path / "deja-la.txt"
    kept.write_text("x", encoding="utf-8")
    service = ArchiveService(exporter=cast(Any, Exporter()), store=cast(Any, LockedStore()))

    with pytest.raises(OSError, match="locked"):
        service.archive(object(), row.mail, row.decision)

    # A retry must not create a second copy; older files are never touched.
    assert [path.name for path in row.decision.target_path.iterdir()] == ["deja-la.txt"]


def test_html_export_names_every_journal_it_would_replace(tmp_path: Path) -> None:
    rows = []
    for number in ("2025-4893", "2025-4894"):
        (tmp_path / "2025" / number).mkdir(parents=True)
        mail = make_mail(entry_id=number, project_number=number)
        rows.append(make_row(tmp_path, mail).model_copy(update={"mail": mail}))
    first = export_project_correspondence_html(rows, {}, tmp_path)
    assert len(first) == 2

    with pytest.raises(ProjectHtmlExistsError) as caught:
        export_project_correspondence_html(rows, {}, tmp_path)

    assert sorted(caught.value.paths) == sorted(result.html_path for result in first)
    assert isinstance(caught.value, FileExistsError)


def test_report_cell_from_a_mail_is_never_a_formula(tmp_path: Path) -> None:
    row = make_row(tmp_path, make_mail(subject='=HYPERLINK("http://x";"Voir")'))

    record = preview_row_to_report_record(row)

    assert record["subject"].startswith("'=")
    assert record["sender"] == "vente@aciers.ch"


# --- folders, mailbox, Outlook -------------------------------------------------------


def test_folder_edits_stay_inside_one_of_the_three_categories(tmp_path: Path) -> None:
    rows = [make_row(
        tmp_path, make_mail(), target="Fournisseurs/Commande/Acme",
        interlocutor=InterlocutorType.FOURNISSEUR, action=PreviewAction.ARCHIVE,
    )]

    with pytest.raises(ValueError, match="categorie"):
        rename_folder_leaf(rows, "Fournisseurs/Commande", "Divers", projects_root=tmp_path)
    with pytest.raises(ValueError, match="categorie"):
        rename_folder_leaf(rows, "Fournisseurs", "Sous-traitants", projects_root=tmp_path)
    with pytest.raises(ValueError, match="categorie"):
        merge_folder(
            rows, "Fournisseurs/Commande/Acme", "Correspondance/Client X", projects_root=tmp_path,
        )

    merged = merge_folder(
        rows, "Fournisseurs/Commande/Acme", "Fournisseurs/Commande/Acme SA",
        projects_root=tmp_path,
    )
    assert merged[0].decision.target_relative_folder == "Fournisseurs/Commande/Acme SA"


def suggested_proposal() -> SortProposal:
    return SortProposal(
        entry_id="1", source=MailboxSourceKind.INBOX, source_label="Boîte de réception",
        subject="Saison 2024-2025", correspondent="Client", sent_at=None,
        direction=Direction.RECEIVED, status=SortStatus.SUGGESTED,
        references=(ProjectReference("2024-2025", (ReferenceSource.SUBJECT,)),),
        suggestion=ProjectSuggestion(project_number="2025-4893", probability=0.8),
        note="à vérifier avant de cocher.",
    )


def test_refreshing_project_folders_keeps_a_jev_suggestion() -> None:
    proposal = suggested_proposal()

    assert replan_proposal(proposal, {}) == proposal


def test_only_project_shaped_numbers_are_sent_to_projectflow() -> None:
    proposal = SortProposal(
        entry_id="1", source=MailboxSourceKind.INBOX, source_label="Boîte de réception",
        subject="Facture", correspondent="Fournisseur", sent_at=None,
        direction=Direction.RECEIVED, status=SortStatus.MISSING_FOLDER,
        missing_numbers=("2026-5245", "2025-123", "2026-01234"),
    )

    assert missing_project_numbers(MailboxAnalysis(proposals=[proposal])) == ["2026-5245"]


def test_nested_outlook_root_is_split_inside_a_list() -> None:
    assert _split_path(["Boîte de réception/Projets", "2026"]) == [
        "Boîte de réception", "Projets", "2026",
    ]


def test_one_unreadable_mail_does_not_stop_the_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    class Item:
        MessageClass = "IPM.Note"

        def __init__(self, entry_id: str) -> None:
            self.EntryID = entry_id

    class Folder:
        Name = f"{PROJECT} (Marquise)"
        Items = [Item("OK-1"), Item("BROKEN"), Item("OK-2")]

    scanner = OutlookScanner()

    def metadata(item: Any, **_kwargs: Any) -> MailMetadata:
        if item.EntryID == "BROKEN":
            raise RuntimeError("COM error")
        return make_mail(entry_id=item.EntryID)

    monkeypatch.setattr(scanner, "mail_item_to_metadata", metadata)

    scanned = scanner.scan_project_folder_with_items(Folder(), "Boite/2025")

    assert [mail.metadata.entry_id for mail in scanned] == ["OK-1", "OK-2"]
    assert scanner.unreadable_count == 1


# --- text helpers --------------------------------------------------------------------


def test_quoted_history_and_signature_are_cut_with_their_real_accents() -> None:
    quoted = "Bonjour,\nvoici l'offre.\n\nLe 3 mai 2026 à 10:12, Jean Dupont a écrit :\n> ancien"
    signed = "Guten Tag\nAnbei die Offerte.\nMit freundlichen Grüssen\nHans Muster"

    assert clean_body(quoted) == "Bonjour,\nvoici l'offre."
    assert clean_body(signed) == "Guten Tag\nAnbei die Offerte."


def test_log_redaction_masks_keys_and_keeps_ordinary_lines() -> None:
    line = "Export impossible: C:/Projets/Risk-analyse.msg (disk-full)"
    assert redact_secrets(line) == line

    leaked = redact_secrets(
        "401 for key sk-proj-ABCDEFGHIJKLMNOP1234 and Bearer abcdefghijklmnop.1234"
    )
    assert "sk-proj" not in leaked
    assert "abcdefghijklmnop" not in leaked

    try:
        raise RuntimeError("Authorization: sk-live-ABCDEFGHIJKLMNOPQRSTU")
    except RuntimeError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "Echec", (), sys.exc_info())
    assert "sk-live" not in SecretRedactionFormatter("%(message)s").format(record)


def test_generic_mailbox_providers_never_stand_for_one_company() -> None:
    assert is_generic_email_domain("gmx.ch")
    assert is_generic_email_domain("hotmail.fr")
    assert not is_generic_email_domain("aciers.ch")


def test_reminder_still_fires_when_its_minute_was_missed() -> None:
    times = ["09:00", "14:00"]
    sent: set[str] = set()

    key = review_reminder_due_key(datetime(2026, 5, 6, 9, 1, 2), times, 3, sent)

    assert key == "2026-05-06 09:00"
    sent.add(key)
    assert review_reminder_due_key(datetime(2026, 5, 6, 9, 2), times, 3, sent) is None
    assert review_reminder_due_key(datetime(2026, 5, 6, 9, 30), times, 3, set()) is None
    assert review_reminder_due_key(datetime(2026, 5, 6, 9, 0), times, 0, set()) is None


def test_project_folder_to_open_falls_back_to_the_projects_root(tmp_path: Path) -> None:
    project = tmp_path / "2025" / PROJECT
    project.mkdir(parents=True)

    assert project_folder_to_open(tmp_path, PROJECT) == (project, True)
    assert project_folder_to_open(tmp_path, "2025-0001") == (tmp_path, False)
    assert project_folder_to_open(tmp_path, "pas un numero") == (tmp_path, False)
    assert project_folder_to_open(tmp_path / "absent", PROJECT) == (None, False)


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    # The release tag builds an installer named after it: both numbers must agree.
    assert mailflow.__version__ == declared


# --- main window ---------------------------------------------------------------------


def make_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication

    from mailflow import config
    from mailflow.config import AppPaths, AppSettings
    from mailflow.models import AiMode
    from mailflow.ui.main_window import MainWindow

    monkeypatch.setattr(config, "get_openai_api_key", lambda: None)
    monkeypatch.setattr(config, "get_jev_api_key", lambda: None)
    qt_app = QApplication.instance() or QApplication([])
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), local_projects_root=tmp_path)
    controller = AppController(
        scan_service=cast(Any, None),
        preview_pipeline=cast(Any, SimpleNamespace(ai_classifier=None, ai_mode=AiMode.ALL)),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    window = cast(Any, MainWindow(settings, controller=controller))
    return window, controller, settings, qt_app


def test_settings_page_saves_engine_timeouts_and_confidence_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailflow.config import load_settings

    window, controller, settings, qt_app = make_window(tmp_path, monkeypatch)

    window.mailflow_openai_timeout_input.setValue(45.0)
    window.mailflow_jev_timeout_input.setValue(12.5)
    window.mailflow_threshold_slider.setValue(90)
    window.mailflow_save_settings_button.click()

    saved = load_settings(settings.paths.config_file)
    assert saved.openai_timeout_seconds == 45.0
    assert saved.jev_timeout_seconds == 12.5
    assert saved.decision_confidence_threshold == pytest.approx(0.9)
    # The threshold also applies to the session, without a restart.
    assert controller.preview_pipeline.decision_confidence_threshold == pytest.approx(0.9)
    # The AI contract already reviews below 80 %: the control cannot go lower.
    assert window.mailflow_threshold_slider.minimum() == 80
    window.close()
    qt_app.processEvents()


def test_open_project_folder_action_opens_the_folder_of_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailflow.ui import main_window

    project = tmp_path / "2025" / PROJECT
    project.mkdir(parents=True)
    opened: list[Path] = []

    def record_opened(path: Path) -> bool:
        opened.append(path)
        return True

    monkeypatch.setattr(main_window, "open_local_folder", record_opened)
    window, controller, _settings, qt_app = make_window(tmp_path, monkeypatch)
    controller.preview_rows = [make_row(tmp_path, make_mail())]
    window.mailflow_refresh_table()

    window.mailflow_open_folder_action.trigger()

    assert opened == [project]
    assert str(project) in window.mailflow_scan_status_label.text()
    window.close()
    qt_app.processEvents()
