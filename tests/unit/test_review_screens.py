from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.core.contact_directory import OrganizationDirectoryEntry
from mailflow.core.mailbox_sorting import (
    MailboxAnalysis,
    ProjectSuggestion,
    SortProposal,
    SortStatus,
)
from mailflow.core.role_suggestions import RoleSuggestion
from mailflow.models import (
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
from mailflow.outlook.mailbox import MailboxSourceKind, ProjectFolder
from mailflow.ui.directory_review import (
    directory_item_view,
    matches_directory_filter,
    next_without_role,
    split_contact,
    suggestion_headline,
    without_role_count,
)
from mailflow.ui.folder_review import (
    breadcrumb_text,
    company_key,
    find_duplicate_folders,
    folder_role_tag,
    tree_header_text,
)
from mailflow.ui.mailbox_sort_view import mailbox_counts_text, mailbox_destination_view
from mailflow.ui.review_queue import (
    archive_button_text,
    build_queue_update,
    bulk_destination_text,
    bulk_selection_view,
    bulk_validation_problem,
    decision_view,
    destination_category,
    destination_for_role,
    first_queue_index,
    next_review_index,
    percent_html,
    queue_header_text,
    validation_problem,
)


def make_row(
    entry_id: str,
    action: PreviewAction,
    *,
    folder: str = "Correspondance/Atelier Dupont SA",
    email: str = "dupont@atelier-dupont.ch",
    role: InterlocutorType = InterlocutorType.CLIENT,
    project: str = "2026-4893",
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number=project,
        outlook_folder=f"Boite de reception/2026/{project}",
        direction=Direction.RECEIVED,
        subject=f"Mail {entry_id}",
        sender_name="Dupont",
        sender_email=email,
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(rule=RuleClassification(confidence=0.7)),
        decision=ArchiveDecision(
            mail_id=entry_id,
            project_number=project,
            archive=action == PreviewAction.ARCHIVE,
            requires_review=action == PreviewAction.REVIEW,
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=role,
            target_relative_folder=folder,
            target_path=Path("/tmp"),
            confidence=0.72,
            duplicate_status="none",
            reason="Role a confirmer.",
        ),
        action=action,
    )


def test_queue_header_counts_review_and_treated_mails() -> None:
    rows = [
        make_row("a", PreviewAction.ARCHIVE),
        make_row("b", PreviewAction.REVIEW),
        make_row("c", PreviewAction.ARCHIVED),
    ]

    assert queue_header_text(rows) == "2026-4893 · 1 à vérifier · 2 traités"
    assert queue_header_text([]) == "Aucun mail analysé"
    assert archive_button_text(0) == "Archiver"
    assert archive_button_text(1) == "Archiver 1 mail"
    assert archive_button_text(4) == "Archiver 4 mails"


def test_queue_opens_first_review_and_moves_to_next_review_with_wraparound() -> None:
    rows = [
        make_row("a", PreviewAction.ARCHIVE),
        make_row("b", PreviewAction.REVIEW),
        make_row("c", PreviewAction.ARCHIVE),
        make_row("d", PreviewAction.REVIEW),
    ]

    assert first_queue_index(rows) == 1
    assert next_review_index(rows, 1) == 3
    assert next_review_index(rows, 3) == 1
    assert next_review_index([make_row("a", PreviewAction.REVIEW)], 0) is None
    assert first_queue_index([make_row("a", PreviewAction.ARCHIVE)]) == 0
    assert first_queue_index([]) is None


def test_decision_rules_follow_the_business_roles() -> None:
    client = InterlocutorType.CLIENT
    supplier = InterlocutorType.FOURNISSEUR

    assert validation_problem("Correspondance", client) is None
    assert validation_problem("Fournisseurs/Commande", supplier) is None
    assert validation_problem(None, client) == "Choisissez une destination."
    assert "jamais en Correspondance" in str(validation_problem("Correspondance", supplier))
    assert "toujours en Correspondance" in str(validation_problem("Fournisseurs/Commande", client))
    assert bulk_validation_problem("Correspondance", None) == "Choisissez le rôle de l'entreprise."
    assert destination_for_role("Fournisseurs/Commande", client) == "Correspondance"
    assert destination_for_role("Correspondance", supplier) is None
    assert destination_for_role("Fournisseurs/Commande", supplier) == "Fournisseurs/Commande"


def test_queue_update_maps_destination_to_category() -> None:
    update = build_queue_update(
        "Fournisseurs/Demande de prix", None, InterlocutorType.FOURNISSEUR
    )

    assert update.mail_type == MailType.DEMANDE_DE_PRIX
    assert update.interlocutor == InterlocutorType.FOURNISSEUR
    assert update.target_relative_folder == "Fournisseurs/Demande de prix"


def test_decision_view_and_destination_of_a_row() -> None:
    row = make_row("a", PreviewAction.REVIEW)

    view = decision_view(row, "jev")
    assert view.kicker == "Proposition MailFlow"
    assert view.confidence == 0.72
    assert destination_category(row) == "Correspondance"
    assert destination_category(make_row("b", PreviewAction.REVIEW, folder="A verifier")) is None
    assert percent_html(0.72).startswith("72<span")


def test_bulk_selection_names_the_shared_company_and_skips_archived_mails() -> None:
    rows = [
        make_row("a", PreviewAction.REVIEW, folder="Correspondance/Régie Lémanique"),
        make_row("b", PreviewAction.ARCHIVE, folder="Correspondance/Régie Lémanique"),
        make_row("c", PreviewAction.ARCHIVED, folder="Correspondance/Autre SA"),
    ]

    view = bulk_selection_view(rows)

    assert view.editable_count == 2
    assert view.archived_count == 1
    assert view.company_line == "Même entreprise : Régie Lémanique"
    assert (
        bulk_destination_text("Correspondance", view.companies)
        == "Correspondance/Régie Lémanique"
    )
    assert bulk_destination_text(None, view.companies) == "À choisir"


def test_duplicate_folders_are_siblings_with_the_same_company_name() -> None:
    supplier = InterlocutorType.FOURNISSEUR
    rows = [
        make_row("a", PreviewAction.ARCHIVE, folder="Fournisseurs/Demande de prix/Alu Profil",
                 email="m@aluprofil.ch", role=supplier),
        *(
            make_row(f"b{index}", PreviewAction.ARCHIVE,
                     folder="Fournisseurs/Demande de prix/Alu-Profil SA",
                     email="m@aluprofil.ch", role=supplier)
            for index in range(3)
        ),
        # Same company under another parent: a command, not a duplicate.
        make_row("c", PreviewAction.ARCHIVE, folder="Fournisseurs/Commande/Alu-Profil SA",
                 email="m@aluprofil.ch", role=supplier),
    ]

    hints = find_duplicate_folders(rows)

    assert company_key("Alu-Profil SA") == company_key("alu profil") == "aluprofil"
    assert len(hints) == 1
    assert hints[0].source == "Fournisseurs/Demande de prix/Alu Profil"
    assert hints[0].target == "Fournisseurs/Demande de prix/Alu-Profil SA"
    assert hints[0].target_count == 3
    assert hints[0].shared_domain == "aluprofil.ch"
    assert find_duplicate_folders(rows, ignored={hints[0].source}) == []


def test_folder_texts() -> None:
    assert folder_role_tag("Correspondance/Atelier Dupont SA") == "client"
    assert folder_role_tag("Fournisseurs/Commande/Alu-Profil SA") == "fournisseur"
    assert folder_role_tag("Fournisseurs/Commande") == ""
    assert folder_role_tag("Correspondance") == ""
    assert breadcrumb_text("Fournisseurs/Demande de prix/Alu Profil") == (
        "Fournisseurs / Demande de prix /"
    )
    assert tree_header_text([make_row("a", PreviewAction.ARCHIVE)]) == (
        "2026-4893 · 1 dossiers · 1 mails"
    )


def directory_entries() -> list[OrganizationDirectoryEntry]:
    return [
        OrganizationDirectoryEntry(1, "Atelier Dupont SA", ("atelier-dupont.ch",), (), 3,
                                   InterlocutorType.CLIENT),
        OrganizationDirectoryEntry(2, "Régie Lémanique", ("regie-leman.ch",),
                                   ("C. Perret <c.perret@regie-leman.ch>",), 2),
        OrganizationDirectoryEntry(3, "Ferrures Berger", ("berger-ferrures.ch",), (), 1),
    ]


def test_directory_filter_and_next_company_without_role() -> None:
    entries = directory_entries()

    assert [
        entry.name for entry in entries
        if matches_directory_filter(entry, "regie", without_role_only=False)
    ] == ["Régie Lémanique"]
    assert [
        entry.organization_id for entry in entries
        if matches_directory_filter(entry, "", without_role_only=True)
    ] == [2, 3]
    assert matches_directory_filter(entries[1], "perret", without_role_only=False)
    assert without_role_count(entries) == 2
    assert next_without_role(entries, 2) == 3
    assert next_without_role(entries, 3) == 2
    assert next_without_role(entries, None) == 2


def test_directory_item_shows_a_suggestion_only_without_role() -> None:
    entries = directory_entries()
    suggestion = RoleSuggestion(2, "client", 0.86, 5)

    assert directory_item_view(entries[1], suggestion).suggestion_text == (
        "Jev suggère : client · 86%"
    )
    assert directory_item_view(entries[0], suggestion).suggestion_text == ""
    assert suggestion_headline(suggestion) == "Probablement <b>client</b>."
    assert split_contact("C. Perret <c.perret@regie-leman.ch>") == (
        "C. Perret", "c.perret@regie-leman.ch"
    )
    assert split_contact("info@regie-leman.ch") == ("", "info@regie-leman.ch")


def test_mailbox_destination_panel_texts() -> None:
    folders = {
        number: ProjectFolder(number, f"{number} Projet", number, None)
        for number in ("2026-4893", "2026-4902")
    }

    def proposal(status: SortStatus, **values: object) -> SortProposal:
        return SortProposal(
            "A", MailboxSourceKind.INBOX, "Boîte de réception", "Coordination", "Martin",
            datetime(2026, 5, 2), Direction.RECEIVED, status, **values,  # type: ignore[arg-type]
        )

    copied = mailbox_destination_view(
        proposal(SortStatus.READY, destinations=("2026-4893", "2026-4902")), folders
    )
    assert copied.title == "Copié dans 2 projets"
    assert copied.lines == ("Original → 2026-4893 Projet", "Copie → 2026-4902 Projet")
    suggested = mailbox_destination_view(
        proposal(SortStatus.SUGGESTED, suggestion=ProjectSuggestion("2026-4893", 0.74)),
        folders,
    )
    assert suggested.title == "Suggestion Jev · 74%"
    assert "jamais cochée" in suggested.note
    assert mailbox_destination_view(proposal(SortStatus.NO_NUMBER), folders).title == (
        "Sans numéro"
    )
    analysis = MailboxAnalysis(proposals=[
        proposal(SortStatus.READY, destinations=("2026-4893",)),
        proposal(SortStatus.NO_NUMBER),
    ])
    assert mailbox_counts_text(analysis, days=30) == "30 derniers jours · 2 mails · 1 prêt"
    assert mailbox_counts_text(None, days=30) == "Aucune analyse pour le moment"
