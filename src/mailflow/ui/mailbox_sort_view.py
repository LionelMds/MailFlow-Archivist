"""Texts of the "Boîte mail" page, which sorts loose Outlook mails into project folders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from mailflow.core.mailbox_sorting import (
    MailboxAnalysis,
    MailboxSortResult,
    SortProposal,
    SortStatus,
)
from mailflow.core.projectflow_link import ProjectFlowReport
from mailflow.outlook.mailbox import MailboxSourceKind, ProjectFolder

MAILBOX_SORT_COLUMNS = (
    "Ranger", "Source", "Date", "Interlocuteur", "Objet", "Numéros", "Trouvé dans",
    "Destination Outlook", "État",
)
CHECK_COLUMN = 0
SOURCE_LABELS = {MailboxSourceKind.INBOX: "Réception", MailboxSourceKind.SENT: "Envoyés"}
STATUS_LABELS = {
    SortStatus.READY: "Prêt à ranger",
    SortStatus.SUGGESTED: "Suggestion Jev",
    SortStatus.MISSING_FOLDER: "Dossier absent",
    SortStatus.NO_NUMBER: "Sans numéro",
    SortStatus.UNREADABLE: "Illisible",
}
STATUS_FILTERS = (
    ("Tous les mails", None),
    ("Prêts à ranger", SortStatus.READY),
    ("Suggestions Jev", SortStatus.SUGGESTED),
    ("Dossier absent", SortStatus.MISSING_FOLDER),
    ("Sans numéro", SortStatus.NO_NUMBER),
)
PERIOD_OPTIONS = (
    (7, "7 derniers jours"),
    (30, "30 derniers jours"),
    (90, "90 derniers jours"),
    (365, "12 derniers mois"),
    (0, "Tous les mails"),
)


def mailbox_since(days: int, *, now: datetime | None = None) -> datetime | None:
    if days <= 0:
        return None
    start = (now or datetime.now()) - timedelta(days=days)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def period_label(days: int) -> str:
    for value, label in PERIOD_OPTIONS:
        if value == days:
            return label
    return "Tous les mails" if days <= 0 else f"{days} derniers jours"


def folder_label(number: str, project_folders: Mapping[str, ProjectFolder]) -> str:
    folder = project_folders.get(number)
    return folder.folder_name if folder is not None else number


def destination_label(
    proposal: SortProposal,
    project_folders: Mapping[str, ProjectFolder],
) -> str:
    if proposal.status == SortStatus.READY:
        first, *copies = proposal.destinations
        label = folder_label(first, project_folders)
        if copies:
            names = ", ".join(folder_label(number, project_folders) for number in copies)
            label += f" + copie dans {names}"
        return label
    if proposal.status == SortStatus.SUGGESTED and proposal.suggestion is not None:
        suggestion = proposal.suggestion
        return (
            f"{folder_label(suggestion.project_number, project_folders)} "
            f"(Jev {suggestion.probability:.0%})"
        )
    if proposal.status == SortStatus.MISSING_FOLDER:
        return "Absent : " + ", ".join(proposal.missing_numbers)
    return ""


def proposal_to_cells(
    proposal: SortProposal,
    project_folders: Mapping[str, ProjectFolder],
) -> list[str]:
    sources = sorted(
        {source.value for reference in proposal.references for source in reference.sources}
    )
    state = STATUS_LABELS[proposal.status]
    return [
        "",
        SOURCE_LABELS.get(proposal.source, proposal.source_label),
        proposal.sent_at.strftime("%d.%m.%Y %H:%M") if proposal.sent_at else "",
        proposal.correspondent,
        proposal.subject or "(sans objet)",
        ", ".join(reference.number for reference in proposal.references),
        ", ".join(sources),
        destination_label(proposal, project_folders),
        f"{state} — {proposal.note}" if proposal.note else state,
    ]


def summarize_mailbox_analysis(analysis: MailboxAnalysis, *, days: int) -> str:
    counts = {status: 0 for status in SortStatus}
    for proposal in analysis.proposals:
        counts[proposal.status] += 1
    copies = sum(
        len(proposal.destinations) - 1
        for proposal in analysis.proposals
        if proposal.status == SortStatus.READY
    )
    parts = [f"{counts[SortStatus.READY]} mail(s) prêt(s) à ranger"]
    if copies:
        parts[0] += f" ({copies} copie(s))"
    if counts[SortStatus.SUGGESTED]:
        parts.append(f"{counts[SortStatus.SUGGESTED]} suggestion(s) Jev à vérifier")
    parts.append(f"{counts[SortStatus.MISSING_FOLDER]} dossier(s) projet absent(s)")
    parts.append(f"{counts[SortStatus.NO_NUMBER]} sans numéro")
    if counts[SortStatus.UNREADABLE]:
        parts.append(f"{counts[SortStatus.UNREADABLE]} illisible(s)")
    summary = " · ".join(parts) + f" — {period_label(days)}."
    if analysis.cancelled:
        summary = "Analyse interrompue. " + summary
    return summary


def build_mailbox_sort_confirmation(choices: Mapping[str, Sequence[str]]) -> str:
    mail_count = len(choices)
    copy_count = sum(max(0, len(numbers) - 1) for numbers in choices.values())
    message = f"Ranger {mail_count} mail(s) dans leur dossier projet Outlook ?"
    if copy_count:
        message += (
            f"\n\n{copy_count} copie(s) seront créées pour les mails qui citent plusieurs "
            "projets : l'original va dans le premier projet cité."
        )
    return message + "\n\nLes mails sont déplacés, jamais supprimés."


def format_mailbox_sort_result(result: MailboxSortResult) -> str:
    message = f"Rangement Outlook : {result.moved_count} mail(s) déplacé(s)"
    if result.copy_count:
        message += f", {result.copy_count} copie(s) créée(s)"
    message += "."
    if result.failures:
        message += f" {len(result.failures)} échec(s) : " + " ; ".join(result.failures[:3])
    return message


def build_projectflow_confirmation(numbers: Sequence[str]) -> str:
    shown = ", ".join(numbers[:10])
    if len(numbers) > 10:
        shown += f" et {len(numbers) - 10} autre(s)"
    return (
        f"Demander à ProjectFlow de créer le dossier Outlook de {len(numbers)} projet(s) ?"
        f"\n\n{shown}\n\nProjectFlow ne traite que les projets du répertoire chantier et "
        "nomme le dossier comme à la création du projet. Aucun mail n'est déplacé à cette "
        "étape."
    )


def format_projectflow_report(report: ProjectFlowReport) -> str:
    parts = []
    if report.ready:
        parts.append(f"{len(report.ready)} dossier(s) projet prêt(s)")
    if report.unknown:
        numbers = ", ".join(number for number, _reason in report.unknown)
        parts.append(
            f"{len(report.unknown)} projet(s) absent(s) du répertoire chantier ({numbers})"
        )
    if report.failed:
        numbers = ", ".join(number for number, _reason in report.failed)
        parts.append(f"{len(report.failed)} échec(s) ({numbers})")
    if report.elsewhere:
        numbers = ", ".join(report.elsewhere)
        parts.append(
            f"{len(report.elsewhere)} dossier(s) créé(s) hors du dossier source analysé "
            f"({numbers}) : vérifiez le compte Outlook choisi dans ProjectFlow"
        )
    if not parts:
        return "ProjectFlow : aucun dossier traité."
    return "ProjectFlow : " + " · ".join(parts) + "."
