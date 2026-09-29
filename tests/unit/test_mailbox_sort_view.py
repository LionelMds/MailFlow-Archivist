from __future__ import annotations

from datetime import datetime
from typing import Any

from mailflow.core.mailbox_sorting import (
    MailboxAnalysis,
    MailboxSortResult,
    ProjectSuggestion,
    SortProposal,
    SortStatus,
)
from mailflow.core.project_references import ProjectReference, ReferenceSource
from mailflow.models import Direction
from mailflow.outlook.mailbox import MailboxSourceKind, ProjectFolder
from mailflow.ui.mailbox_sort_view import (
    build_mailbox_sort_confirmation,
    destination_label,
    format_mailbox_sort_result,
    mailbox_since,
    period_label,
    proposal_to_cells,
    summarize_mailbox_analysis,
)

FOLDERS = {
    number: ProjectFolder(number, name, f"Inbox/2025/{number}", None)
    for number, name in (("2025-4893", "2025-4893 Villa Dupont"), ("2025-5012", "2025-5012"))
}


def proposal(status: SortStatus, **values: Any) -> SortProposal:
    base: dict[str, Any] = {
        "entry_id": "A",
        "source": MailboxSourceKind.INBOX,
        "source_label": "Boîte de réception",
        "subject": "Offre balcons",
        "correspondent": "Dupont",
        "sent_at": datetime(2026, 9, 28, 10, 30),
        "direction": Direction.RECEIVED,
        "status": status,
    }
    return SortProposal(**(base | values))


def test_cells_show_numbers_sources_destination_and_state() -> None:
    ready = proposal(
        SortStatus.READY,
        references=(
            ProjectReference("2025-4893", (ReferenceSource.SUBJECT, ReferenceSource.BODY)),
            ProjectReference("2025-5012", (ReferenceSource.ATTACHMENT_CONTENT,)),
        ),
        destinations=("2025-4893", "2025-5012"),
    )

    assert proposal_to_cells(ready, FOLDERS) == [
        "",
        "Réception",
        "28.09.2026 10:30",
        "Dupont",
        "Offre balcons",
        "2025-4893, 2025-5012",
        "contenu de pièce jointe, corps, objet",
        "2025-4893 Villa Dupont + copie dans 2025-5012",
        "Prêt à ranger",
    ]


def test_destination_labels_for_each_state() -> None:
    suggested = proposal(
        SortStatus.SUGGESTED, suggestion=ProjectSuggestion("2025-4893", 0.87),
        note="à vérifier avant de cocher.",
    )
    missing = proposal(SortStatus.MISSING_FOLDER, missing_numbers=("2026-0100",))

    assert destination_label(suggested, FOLDERS) == "2025-4893 Villa Dupont (Jev 87%)"
    assert destination_label(missing, FOLDERS) == "Absent : 2026-0100"
    assert destination_label(proposal(SortStatus.NO_NUMBER), FOLDERS) == ""
    assert proposal_to_cells(suggested, FOLDERS)[-1] == (
        "Suggestion Jev — à vérifier avant de cocher."
    )
    pending = proposal(SortStatus.NO_NUMBER, source=MailboxSourceKind.PENDING,
                       source_label="A CLASSER")
    assert proposal_to_cells(pending, FOLDERS)[1] == "A CLASSER"
    assert proposal_to_cells(proposal(SortStatus.NO_NUMBER, subject=""), FOLDERS)[4] == (
        "(sans objet)"
    )


def test_summary_counts_each_state_and_the_period() -> None:
    analysis = MailboxAnalysis(proposals=[
        proposal(SortStatus.READY, destinations=("2025-4893", "2025-5012")),
        proposal(SortStatus.READY, destinations=("2025-4893",)),
        proposal(SortStatus.SUGGESTED),
        proposal(SortStatus.MISSING_FOLDER),
        proposal(SortStatus.NO_NUMBER),
        proposal(SortStatus.UNREADABLE),
    ])

    assert summarize_mailbox_analysis(analysis, days=90) == (
        "2 mail(s) prêt(s) à ranger (1 copie(s)) · 1 suggestion(s) Jev à vérifier · "
        "1 dossier(s) projet absent(s) · 1 sans numéro · 1 illisible(s) — 90 derniers jours."
    )
    analysis.cancelled = True
    assert summarize_mailbox_analysis(analysis, days=0).startswith("Analyse interrompue.")


def test_confirmation_explains_copies_and_that_nothing_is_deleted() -> None:
    message = build_mailbox_sort_confirmation({
        "A": ("2025-4893", "2025-5012"), "B": ("2025-4893",),
    })

    assert message.startswith("Ranger 2 mail(s) dans leur dossier projet Outlook ?")
    assert "1 copie(s)" in message
    assert "jamais supprimés" in message
    assert "copie" not in build_mailbox_sort_confirmation({"A": ("2025-4893",)})


def test_result_message_lists_moves_copies_and_failures() -> None:
    assert format_mailbox_sort_result(MailboxSortResult(moved_count=3, copy_count=1)) == (
        "Rangement Outlook : 3 mail(s) déplacé(s), 1 copie(s) créée(s)."
    )
    failed = MailboxSortResult(moved_count=0, failures=["Offre : refus"])
    assert format_mailbox_sort_result(failed) == (
        "Rangement Outlook : 0 mail(s) déplacé(s). 1 échec(s) : Offre : refus"
    )


def test_period_helpers() -> None:
    now = datetime(2026, 9, 29, 15, 45)

    assert mailbox_since(0, now=now) is None
    assert mailbox_since(30, now=now) == datetime(2026, 8, 30)
    assert period_label(365) == "12 derniers mois"
    assert period_label(45) == "45 derniers jours"
    assert period_label(0) == "Tous les mails"
