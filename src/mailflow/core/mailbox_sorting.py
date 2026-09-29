"""Sort loose Outlook mails into the project folder whose number they quote.

The inbox, the "A CLASSER" folder and the sent items are read. A project number
(20XX-XXXX) found in the subject, the body or an attachment names the destination.
Nothing moves before the user confirms, and a mail is never deleted: it is moved to
its project folder, and copied into each additional project it quotes.

A mail without a number can receive a project suggestion (Jev). A suggestion is never
selected automatically: the user decides.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from mailflow.core.contact_directory import email_domain, is_internal_domain, split_contact
from mailflow.core.project_references import (
    ProjectReference,
    ReferenceSource,
    find_project_numbers,
    is_year_range,
    merge_references,
)
from mailflow.models import Direction, MailMetadata
from mailflow.outlook.mailbox import (
    DEFAULT_PENDING_FOLDER,
    MailboxMail,
    MailboxSource,
    MailboxSourceKind,
    ProjectFolder,
)

MAX_PROJECT_CANDIDATES = 12

# Called with (current, total, message); returning False stops the analysis.
MailboxProgress = Callable[[int, int, str], bool]

logger = logging.getLogger(__name__)


class SortStatus(StrEnum):
    READY = "ready"
    SUGGESTED = "suggested"
    MISSING_FOLDER = "missing_folder"
    NO_NUMBER = "no_number"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ProjectCandidate:
    project_number: str
    folder_name: str
    organizations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectSuggestion:
    project_number: str
    probability: float


class ProjectSuggestionError(RuntimeError):
    """A locally worded failure of the suggestion engine, safe to display."""


class ProjectSuggester(Protocol):
    def suggest(
        self,
        mail: MailMetadata,
        candidates: Sequence[ProjectCandidate],
    ) -> ProjectSuggestion | None:
        ...


class ProjectHistoryProtocol(Protocol):
    def projects_for_email(self, email: str) -> list[str]:
        ...

    def organization_name_for_email(self, email: str) -> str | None:
        ...


class MailboxProtocol(Protocol):
    def inbox(self, *, account_identifier: str | None, outlook_root_folder: str) -> Any:
        ...

    def sources(
        self,
        *,
        account_identifier: str | None,
        outlook_root_folder: str,
        pending_folder_name: str,
        kinds: frozenset[MailboxSourceKind],
    ) -> tuple[list[MailboxSource], list[str]]:
        ...

    def project_folders(
        self, inbox: Any, *, outlook_root_folder: str,
    ) -> dict[str, ProjectFolder]:
        ...

    def mail_items(self, folder: Any, *, since: datetime | None) -> list[Any]:
        ...

    def read_mail(self, item: Any, source: MailboxSource) -> MailboxMail:
        ...

    def attachment_texts(self, item: Any) -> list[str]:
        ...

    def move(self, item: Any, folder: Any) -> None:
        ...

    def copy(self, item: Any, folder: Any) -> None:
        ...

    def display(self, item: Any) -> None:
        ...


@dataclass(frozen=True)
class MailboxSortRequest:
    account_identifier: str | None
    outlook_root_folder: str
    pending_folder_name: str = DEFAULT_PENDING_FOLDER
    sources: frozenset[MailboxSourceKind] = frozenset(MailboxSourceKind)
    since: datetime | None = None
    read_attachment_contents: bool = True


@dataclass(frozen=True)
class SortProposal:
    entry_id: str
    source: MailboxSourceKind
    source_label: str
    subject: str
    correspondent: str
    sent_at: datetime | None
    direction: Direction
    status: SortStatus
    references: tuple[ProjectReference, ...] = ()
    destinations: tuple[str, ...] = ()
    missing_numbers: tuple[str, ...] = ()
    suggestion: ProjectSuggestion | None = None
    note: str = ""

    @property
    def selectable_targets(self) -> tuple[str, ...]:
        """Project numbers this mail may go to once the user ticks it."""
        if self.status == SortStatus.READY:
            return self.destinations
        if self.status == SortStatus.SUGGESTED and self.suggestion is not None:
            return (self.suggestion.project_number,)
        return ()

    @property
    def selected_by_default(self) -> bool:
        # Only a number written in the mail selects it; a suggestion waits for the user.
        return self.status == SortStatus.READY


@dataclass
class MailboxAnalysis:
    proposals: list[SortProposal] = field(default_factory=list)
    project_folders: dict[str, ProjectFolder] = field(default_factory=dict)
    items: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class MailboxSortResult:
    moved_count: int = 0
    copy_count: int = 0
    sorted_entry_ids: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def plan_proposal(
    *,
    entry_id: str,
    source: MailboxSource,
    mail: MailMetadata,
    references: Sequence[ProjectReference],
    project_folders: Mapping[str, ProjectFolder],
) -> SortProposal:
    destinations = tuple(ref.number for ref in references if ref.number in project_folders)
    missing = tuple(
        ref.number for ref in references
        if ref.number not in project_folders and not is_year_range(ref.number)
    )
    if destinations:
        status = SortStatus.READY
        note = f"Sans dossier Outlook, ignoré : {', '.join(missing)}" if missing else ""
    elif missing:
        status = SortStatus.MISSING_FOLDER
        note = "créez le dossier dans Outlook puis relancez l'analyse."
    else:
        status = SortStatus.NO_NUMBER
        note = ""
    return SortProposal(
        entry_id=entry_id,
        source=source.kind,
        source_label=source.label,
        subject=mail.subject,
        correspondent=correspondent_label(mail),
        sent_at=mail.sent_at,
        direction=mail.direction,
        status=status,
        references=tuple(references),
        destinations=destinations,
        missing_numbers=missing,
        note=note,
    )


def correspondent_label(mail: MailMetadata) -> str:
    if mail.direction == Direction.SENT:
        recipient = mail.recipients[0] if mail.recipients else ""
        return f"À : {recipient}" if recipient else "À : (aucun destinataire)"
    return mail.sender_name or mail.sender_email


def external_emails(mail: MailMetadata) -> list[str]:
    """External addresses of the exchange, the main correspondent first."""
    contacts = (
        list(mail.recipients) if mail.direction == Direction.SENT
        else [mail.sender_email or mail.sender_name, *mail.recipients]
    )
    emails: dict[str, None] = {}
    for contact in contacts:
        _display_name, email = split_contact(contact)
        domain = email_domain(email)
        if domain is not None and not is_internal_domain(domain):
            emails.setdefault(email, None)
    return list(emails)


def project_candidates(
    mail: MailMetadata,
    project_folders: Mapping[str, ProjectFolder],
    history: ProjectHistoryProtocol,
    *,
    limit: int = MAX_PROJECT_CANDIDATES,
) -> list[ProjectCandidate]:
    """Projects with an Outlook folder where the mail's companies already exchanged."""
    organizations: dict[str, list[str]] = {}
    for email in external_emails(mail):
        organization = history.organization_name_for_email(email) or email
        for number in history.projects_for_email(email):
            if number not in project_folders:
                continue
            names = organizations.setdefault(number, [])
            if organization not in names:
                names.append(organization)
    return [
        ProjectCandidate(
            project_number=number,
            folder_name=project_folders[number].folder_name,
            organizations=tuple(names),
        )
        for number, names in list(organizations.items())[:limit]
    ]


class MailboxSortService:
    def __init__(self, mailbox: MailboxProtocol) -> None:
        self.mailbox = mailbox

    def analyze(
        self,
        request: MailboxSortRequest,
        *,
        progress: MailboxProgress | None = None,
        suggester: ProjectSuggester | None = None,
        history: ProjectHistoryProtocol | None = None,
    ) -> MailboxAnalysis:
        inbox = self.mailbox.inbox(
            account_identifier=request.account_identifier,
            outlook_root_folder=request.outlook_root_folder,
        )
        sources, warnings = self.mailbox.sources(
            account_identifier=request.account_identifier,
            outlook_root_folder=request.outlook_root_folder,
            pending_folder_name=request.pending_folder_name,
            kinds=request.sources,
        )
        analysis = MailboxAnalysis(
            project_folders=self.mailbox.project_folders(
                inbox, outlook_root_folder=request.outlook_root_folder,
            ),
            warnings=warnings,
        )
        if not analysis.project_folders:
            analysis.warnings.append(
                f"Aucun dossier projet Outlook trouvé sous « {request.outlook_root_folder} »."
            )
        pending = [
            (source, item)
            for source in sources
            for item in self.mailbox.mail_items(source.folder, since=request.since)
        ]
        mails: dict[str, MailMetadata] = {}
        total = len(pending)
        for index, (source, item) in enumerate(pending, start=1):
            if progress is not None and not progress(
                index, total, f"Lecture des mails {index}/{total}..."
            ):
                analysis.cancelled = True
                return analysis
            entry_id = str(getattr(item, "EntryID", "") or "") or f"#{index}"
            try:
                proposal, mail = self._read_proposal(
                    entry_id, item, source, analysis.project_folders,
                    read_attachment_contents=request.read_attachment_contents,
                )
            except Exception as exc:
                logger.warning("Mail Outlook illisible pour le rangement", exc_info=True)
                proposal = _unreadable_proposal(entry_id, item, source, exc)
            else:
                mails[entry_id] = mail
            analysis.items[entry_id] = item
            analysis.proposals.append(proposal)
        if suggester is not None and history is not None:
            self._suggest_projects(analysis, mails, suggester, history, progress)
        return analysis

    def execute(
        self,
        analysis: MailboxAnalysis,
        choices: Mapping[str, Sequence[str]],
    ) -> MailboxSortResult:
        """Move each chosen mail to its first project, and copy it into the others."""
        result = MailboxSortResult()
        proposals = {proposal.entry_id: proposal for proposal in analysis.proposals}
        for entry_id, numbers in choices.items():
            proposal = proposals.get(entry_id)
            item = analysis.items.get(entry_id)
            if proposal is None or item is None:
                continue
            allowed = set(proposal.selectable_targets)
            targets = [
                number for number in dict.fromkeys(numbers)
                if number in allowed and number in analysis.project_folders
            ]
            if not targets:
                continue
            try:
                # Copies are made first: once moved, the original may not be reachable.
                for number in targets[1:]:
                    self.mailbox.copy(item, analysis.project_folders[number].folder)
                    result.copy_count += 1
                self.mailbox.move(item, analysis.project_folders[targets[0]].folder)
            except Exception as exc:
                result.failures.append(f"{proposal.subject or entry_id} : {exc}")
                continue
            result.moved_count += 1
            result.sorted_entry_ids.append(entry_id)
        return result

    def display(self, analysis: MailboxAnalysis, entry_id: str) -> None:
        item = analysis.items.get(entry_id)
        if item is None:
            raise LookupError("Ce mail n'est plus dans la liste analysée.")
        self.mailbox.display(item)

    def _read_proposal(
        self,
        entry_id: str,
        item: Any,
        source: MailboxSource,
        project_folders: Mapping[str, ProjectFolder],
        *,
        read_attachment_contents: bool,
    ) -> tuple[SortProposal, MailMetadata]:
        read = self.mailbox.read_mail(item, source)
        found: list[tuple[ReferenceSource, list[str]]] = [
            (ReferenceSource.SUBJECT, find_project_numbers(read.metadata.subject)),
            (ReferenceSource.BODY, find_project_numbers(read.body)),
            (
                ReferenceSource.ATTACHMENT_NAME,
                find_project_numbers("\n".join(read.metadata.attachment_names)),
            ),
        ]
        references = merge_references(found)
        # Attachment contents are costly to read: only when nothing else leads to a folder.
        if read_attachment_contents and not any(
            reference.number in project_folders for reference in references
        ):
            numbers = [
                number
                for text in self.mailbox.attachment_texts(item)
                for number in find_project_numbers(text)
            ]
            references = merge_references([*found, (ReferenceSource.ATTACHMENT_CONTENT, numbers)])
        proposal = plan_proposal(
            entry_id=entry_id,
            source=source,
            mail=read.metadata,
            references=references,
            project_folders=project_folders,
        )
        return proposal, read.metadata

    def _suggest_projects(
        self,
        analysis: MailboxAnalysis,
        mails: Mapping[str, MailMetadata],
        suggester: ProjectSuggester,
        history: ProjectHistoryProtocol,
        progress: MailboxProgress | None,
    ) -> None:
        waiting = [
            index for index, proposal in enumerate(analysis.proposals)
            if proposal.status == SortStatus.NO_NUMBER and proposal.entry_id in mails
        ]
        for position, index in enumerate(waiting, start=1):
            if progress is not None and not progress(
                position, len(waiting), f"Suggestions Jev {position}/{len(waiting)}..."
            ):
                analysis.cancelled = True
                return
            proposal = analysis.proposals[index]
            mail = mails[proposal.entry_id]
            candidates = project_candidates(mail, analysis.project_folders, history)
            if not candidates:
                analysis.proposals[index] = replace(
                    proposal, note="Aucun projet connu pour cet interlocuteur dans l'annuaire.",
                )
                continue
            try:
                suggestion = suggester.suggest(mail, candidates)
            except ProjectSuggestionError as exc:
                # The same failure would repeat for every mail: stop asking.
                analysis.warnings.append(f"Suggestions Jev interrompues : {exc}")
                return
            except Exception:
                logger.warning("Suggestion de projet impossible", exc_info=True)
                analysis.warnings.append("Suggestions Jev interrompues : erreur inattendue.")
                return
            if suggestion is None or suggestion.project_number not in analysis.project_folders:
                analysis.proposals[index] = replace(
                    proposal, note="Jev : aucun projet proposé ne correspond avec certitude.",
                )
                continue
            analysis.proposals[index] = replace(
                proposal,
                status=SortStatus.SUGGESTED,
                suggestion=suggestion,
                note="à vérifier avant de cocher.",
            )


def _unreadable_proposal(
    entry_id: str, item: Any, source: MailboxSource, error: Exception,
) -> SortProposal:
    return SortProposal(
        entry_id=entry_id,
        source=source.kind,
        source_label=source.label,
        subject=str(getattr(item, "Subject", "") or ""),
        correspondent=str(getattr(item, "SenderName", "") or ""),
        sent_at=None,
        direction=(
            Direction.SENT if source.kind == MailboxSourceKind.SENT else Direction.RECEIVED
        ),
        status=SortStatus.UNREADABLE,
        note=f"Mail illisible : {type(error).__name__}",
    )
