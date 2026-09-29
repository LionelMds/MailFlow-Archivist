from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mailflow.core.app_controller import AppController
from mailflow.core.mailbox_sorting import (
    MailboxAnalysis,
    MailboxSortRequest,
    MailboxSortService,
    ProjectCandidate,
    ProjectSuggestion,
    ProjectSuggestionError,
    SortProposal,
    SortStatus,
    external_emails,
    missing_project_numbers,
    project_candidates,
    replan_proposal,
)
from mailflow.core.project_references import ReferenceSource
from mailflow.models import Direction, MailMetadata
from mailflow.outlook.mailbox import (
    MailboxMail,
    MailboxSource,
    MailboxSourceKind,
    ProjectFolder,
)

INBOX = MailboxSource(MailboxSourceKind.INBOX, "Boîte de réception", "inbox-folder")
PENDING = MailboxSource(MailboxSourceKind.PENDING, "A CLASSER", "pending-folder")
SENT = MailboxSource(MailboxSourceKind.SENT, "Éléments envoyés", "sent-folder")


def mail_item(
    entry_id: str,
    *,
    subject: str = "",
    body: str = "",
    attachments: Sequence[str] = (),
    attachment_texts: Sequence[str] = (),
    sender: str = "achat@fournisseur.test",
    recipients: Sequence[str] = ("lionel@balzmetal.ch",),
    unreadable: bool = False,
) -> Any:
    return SimpleNamespace(
        EntryID=entry_id,
        Subject=subject,
        body=body,
        attachment_names=list(attachments),
        attachment_texts=list(attachment_texts),
        sender=sender,
        recipients=list(recipients),
        unreadable=unreadable,
    )


class FakeMailbox:
    def __init__(
        self,
        items: dict[str, list[Any]],
        folders: Sequence[str] = ("2025-4893", "2025-5012"),
        *,
        warnings: Sequence[str] = (),
    ) -> None:
        self.items = items
        self.folders = {
            number: ProjectFolder(number, f"{number} Chantier", f"Inbox/2025/{number}", number)
            for number in folders
        }
        self.warnings = list(warnings)
        self.calls: list[tuple[str, str, str]] = []
        self.attachment_reads: list[str] = []
        self.requested_kinds: frozenset[MailboxSourceKind] | None = None
        self.since: list[datetime | None] = []
        self.fail_on: set[tuple[str, str]] = set()

    def inbox(self, *, account_identifier: str | None, outlook_root_folder: str) -> Any:
        return "inbox-folder"

    def sources(
        self,
        *,
        account_identifier: str | None,
        outlook_root_folder: str,
        pending_folder_name: str,
        kinds: frozenset[MailboxSourceKind],
    ) -> tuple[list[MailboxSource], list[str]]:
        self.requested_kinds = kinds
        sources = [source for source in (INBOX, PENDING, SENT) if source.kind in kinds]
        return sources, list(self.warnings)

    def project_folders(self, inbox: Any, *, outlook_root_folder: str) -> dict[str, ProjectFolder]:
        return dict(self.folders)

    def mail_items(self, folder: Any, *, since: datetime | None) -> list[Any]:
        self.since.append(since)
        return list(self.items.get(folder, []))

    def read_mail(self, item: Any, source: MailboxSource) -> MailboxMail:
        if item.unreadable:
            raise ValueError("Outlook mail item has no usable date")
        direction = Direction.SENT if source.kind == MailboxSourceKind.SENT else Direction.RECEIVED
        return MailboxMail(
            metadata=MailMetadata(
                entry_id=item.EntryID,
                project_number="",
                outlook_folder=source.label,
                direction=direction,
                subject=item.Subject,
                sender_name="Fournisseur SA",
                sender_email=item.sender,
                recipients=item.recipients,
                sent_at=datetime(2026, 9, 28, 10, 30),
                attachment_names=item.attachment_names,
            ),
            body=item.body,
        )

    def attachment_texts(self, item: Any) -> list[str]:
        self.attachment_reads.append(item.EntryID)
        return list(item.attachment_texts)

    def move(self, item: Any, folder: Any) -> None:
        self._act("move", item, folder)

    def copy(self, item: Any, folder: Any) -> None:
        self._act("copy", item, folder)

    def display(self, item: Any) -> None:
        self.calls.append(("display", item.EntryID, ""))

    def _act(self, action: str, item: Any, folder: Any) -> None:
        if (action, item.EntryID) in self.fail_on:
            raise RuntimeError("Outlook a refuse")
        self.calls.append((action, item.EntryID, str(folder)))


class FakeHistory:
    def __init__(self, projects: dict[str, list[str]], names: dict[str, str]) -> None:
        self.projects = projects
        self.names = names

    def projects_for_email(self, email: str) -> list[str]:
        return self.projects.get(email, [])

    def organization_name_for_email(self, email: str) -> str | None:
        return self.names.get(email)


class FakeSuggester:
    def __init__(
        self, answer: ProjectSuggestion | None = None, error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, list[ProjectCandidate]]] = []

    def suggest(
        self, mail: MailMetadata, candidates: Sequence[ProjectCandidate],
    ) -> ProjectSuggestion | None:
        self.calls.append((mail.entry_id, list(candidates)))
        if self.error is not None:
            raise self.error
        return self.answer


def request(**changes: Any) -> MailboxSortRequest:
    return MailboxSortRequest(
        account_identifier=None, outlook_root_folder="Boite de reception", **changes,
    )


def by_id(analysis: Any) -> dict[str, Any]:
    return {proposal.entry_id: proposal for proposal in analysis.proposals}


def test_numbers_in_subject_body_and_attachment_names_choose_the_folders() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Offre 2025-4893 balcons"),
            mail_item("B", body="Bonjour,\nconcerne votre projet 2025-5012."),
            mail_item("C", attachments=["Confirmation 2025-4893.pdf"]),
        ],
    })

    analysis = MailboxSortService(mailbox).analyze(request())

    proposals = by_id(analysis)
    assert [proposal.status for proposal in analysis.proposals] == [SortStatus.READY] * 3
    assert proposals["A"].destinations == ("2025-4893",)
    assert proposals["A"].references[0].sources == (ReferenceSource.SUBJECT,)
    assert proposals["B"].destinations == ("2025-5012",)
    assert proposals["C"].references[0].sources == (ReferenceSource.ATTACHMENT_NAME,)
    assert all(proposal.selected_by_default for proposal in analysis.proposals)
    # Nothing moves during the analysis.
    assert mailbox.calls == []
    assert mailbox.attachment_reads == []


def test_attachment_contents_are_read_only_when_nothing_else_leads_to_a_folder() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", attachments=["facture.pdf"], attachment_texts=["Réf. 2025-5012"]),
            mail_item("B", subject="2025-4893", attachment_texts=["Réf. 2025-5012"]),
            mail_item("C", subject="Saison 2024-2025", attachment_texts=["Chantier 2025-4893"]),
        ],
    })

    analysis = MailboxSortService(mailbox).analyze(request())

    proposals = by_id(analysis)
    assert mailbox.attachment_reads == ["A", "C"]
    assert proposals["A"].destinations == ("2025-5012",)
    assert proposals["A"].references[0].sources == (ReferenceSource.ATTACHMENT_CONTENT,)
    assert proposals["B"].destinations == ("2025-4893",)
    assert proposals["C"].destinations == ("2025-4893",)
    # A season such as 2024-2025 is neither a destination nor a missing folder.
    assert proposals["C"].missing_numbers == ()


def test_attachment_contents_can_be_skipped() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [mail_item("A", attachment_texts=["Réf. 2025-5012"])],
    })

    analysis = MailboxSortService(mailbox).analyze(request(read_attachment_contents=False))

    assert mailbox.attachment_reads == []
    assert analysis.proposals[0].status == SortStatus.NO_NUMBER


def test_missing_project_folder_leaves_the_mail_in_place() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Nouveau projet 2026-0100"),
            mail_item("B", subject="2025-4893 et 2026-0100"),
        ],
    })

    analysis = MailboxSortService(mailbox).analyze(request())

    proposals = by_id(analysis)
    assert proposals["A"].status == SortStatus.MISSING_FOLDER
    assert proposals["A"].missing_numbers == ("2026-0100",)
    assert proposals["A"].selectable_targets == ()
    assert not proposals["A"].selected_by_default
    assert proposals["B"].status == SortStatus.READY
    assert proposals["B"].destinations == ("2025-4893",)
    assert "2026-0100" in proposals["B"].note


def test_every_selected_source_is_read_with_the_period() -> None:
    since = datetime(2026, 7, 1)
    mailbox = FakeMailbox({
        "inbox-folder": [mail_item("A", subject="2025-4893")],
        "pending-folder": [mail_item("B", subject="2025-4893")],
        "sent-folder": [
            mail_item("C", subject="RE: 2025-5012", sender="lionel@balzmetal.ch",
                      recipients=["Client <chef@client.test>"]),
        ],
    }, warnings=["Dossier Outlook « A CLASSER » introuvable."])

    analysis = MailboxSortService(mailbox).analyze(request(since=since))

    assert mailbox.since == [since, since, since]
    assert [proposal.source for proposal in analysis.proposals] == [
        MailboxSourceKind.INBOX, MailboxSourceKind.PENDING, MailboxSourceKind.SENT,
    ]
    sent = by_id(analysis)["C"]
    assert sent.direction == Direction.SENT
    assert sent.correspondent == "À : Client <chef@client.test>"
    assert analysis.warnings == ["Dossier Outlook « A CLASSER » introuvable."]


def test_unreadable_mail_is_listed_without_stopping_the_analysis() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Brouillon", unreadable=True),
            mail_item("B", subject="2025-4893"),
        ],
    })

    analysis = MailboxSortService(mailbox).analyze(request())

    proposals = by_id(analysis)
    assert proposals["A"].status == SortStatus.UNREADABLE
    assert proposals["A"].selectable_targets == ()
    assert proposals["B"].status == SortStatus.READY


def test_progress_can_cancel_the_analysis() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [mail_item("A", subject="2025-4893"), mail_item("B", subject="2025-4893")],
    })
    seen: list[tuple[int, int]] = []

    def progress(current: int, total: int, _message: str) -> bool:
        seen.append((current, total))
        return current < 2

    analysis = MailboxSortService(mailbox).analyze(request(), progress=progress)

    assert seen == [(1, 2), (2, 2)]
    assert analysis.cancelled
    assert [proposal.entry_id for proposal in analysis.proposals] == ["A"]


def test_no_project_folder_is_reported() -> None:
    mailbox = FakeMailbox({"inbox-folder": []}, folders=())

    analysis = MailboxSortService(mailbox).analyze(request())

    assert analysis.warnings == [
        "Aucun dossier projet Outlook trouvé sous « Boite de reception »."
    ]


def test_a_mail_quoting_two_projects_is_copied_then_moved() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="2025-4893 / 2025-5012"),
            mail_item("B", subject="2025-4893"),
        ],
    })
    service = MailboxSortService(mailbox)
    analysis = service.analyze(request())

    result = service.execute(analysis, {"A": ("2025-4893", "2025-5012"), "B": ("2025-4893",)})

    assert mailbox.calls == [
        ("copy", "A", "2025-5012"),
        ("move", "A", "2025-4893"),
        ("move", "B", "2025-4893"),
    ]
    assert result.moved_count == 2
    assert result.copy_count == 1
    assert result.sorted_entry_ids == ["A", "B"]
    assert result.failures == []


def test_execution_ignores_targets_that_the_analysis_did_not_propose() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="2025-4893"),
            mail_item("B", subject="2026-0100"),
            mail_item("C"),
        ],
    })
    service = MailboxSortService(mailbox)
    analysis = service.analyze(request())

    result = service.execute(analysis, {
        "A": ("2025-5012", "2025-4893"),
        "B": ("2026-0100",),
        "C": ("2025-4893",),
        "unknown": ("2025-4893",),
    })

    assert mailbox.calls == [("move", "A", "2025-4893")]
    assert result.sorted_entry_ids == ["A"]


def test_a_failed_move_is_reported_and_other_mails_continue() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Offre 2025-4893"),
            mail_item("B", subject="2025-5012"),
        ],
    })
    mailbox.fail_on.add(("move", "A"))
    service = MailboxSortService(mailbox)
    analysis = service.analyze(request())

    result = service.execute(analysis, {"A": ("2025-4893",), "B": ("2025-5012",)})

    assert result.sorted_entry_ids == ["B"]
    assert result.failures == ["Offre 2025-4893 : Outlook a refuse"]


def test_external_emails_skip_balz_metal_addresses() -> None:
    received = MailMetadata(
        entry_id="A", project_number="", outlook_folder="Inbox",
        direction=Direction.RECEIVED, sender_email="Achat@Fournisseur.test",
        recipients=["lionel@balzmetal.ch", "Archi <archi@bureau.test>"],
        sent_at=datetime(2026, 9, 28),
    )
    sent = received.model_copy(update={
        "direction": Direction.SENT,
        "recipients": ["collegue@balzmetal.ch", "chef@client.test"],
    })

    assert external_emails(received) == ["achat@fournisseur.test", "archi@bureau.test"]
    assert external_emails(sent) == ["chef@client.test"]


def test_candidates_are_known_projects_of_the_correspondents_with_a_folder() -> None:
    mail = MailMetadata(
        entry_id="A", project_number="", outlook_folder="Inbox",
        direction=Direction.RECEIVED, sender_email="achat@fournisseur.test",
        recipients=["archi@bureau.test"], sent_at=datetime(2026, 9, 28),
    )
    folders = {
        number: ProjectFolder(number, f"{number} Chantier", number, number)
        for number in ("2025-4893", "2025-5012")
    }
    history = FakeHistory(
        {
            "achat@fournisseur.test": ["2025-5012", "2024-0001", "2025-4893"],
            "archi@bureau.test": ["2025-4893"],
        },
        {"achat@fournisseur.test": "Fournisseur SA"},
    )

    candidates = project_candidates(mail, folders, history)

    assert candidates == [
        ProjectCandidate("2025-5012", "2025-5012 Chantier", ("Fournisseur SA",)),
        ProjectCandidate(
            "2025-4893", "2025-4893 Chantier", ("Fournisseur SA", "archi@bureau.test"),
        ),
    ]
    assert project_candidates(mail, folders, history, limit=1) == candidates[:1]


def jev_mailbox() -> FakeMailbox:
    return FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Livraison mardi"),
            mail_item("B", subject="Newsletter", sender="info@inconnu.test"),
            mail_item("C", subject="2025-4893"),
        ],
    })


def jev_history() -> FakeHistory:
    return FakeHistory({"achat@fournisseur.test": ["2025-5012"]}, {})


def test_jev_suggestion_is_offered_but_never_preselected() -> None:
    suggester = FakeSuggester(ProjectSuggestion("2025-5012", 0.91))

    analysis = MailboxSortService(jev_mailbox()).analyze(
        request(), suggester=suggester, history=jev_history(),
    )

    proposals = by_id(analysis)
    assert [call[0] for call in suggester.calls] == ["A"]
    assert proposals["A"].status == SortStatus.SUGGESTED
    assert proposals["A"].selectable_targets == ("2025-5012",)
    assert not proposals["A"].selected_by_default
    assert proposals["A"].suggestion == ProjectSuggestion("2025-5012", 0.91)
    assert proposals["A"].note == "à vérifier avant de cocher."
    assert proposals["B"].status == SortStatus.NO_NUMBER
    assert "Aucun projet connu" in proposals["B"].note
    assert proposals["C"].status == SortStatus.READY


def test_jev_without_certain_answer_leaves_the_mail_without_number() -> None:
    analysis = MailboxSortService(jev_mailbox()).analyze(
        request(), suggester=FakeSuggester(None), history=jev_history(),
    )

    proposal = by_id(analysis)["A"]
    assert proposal.status == SortStatus.NO_NUMBER
    assert "aucun projet proposé" in proposal.note


@pytest.mark.parametrize(
    ("error", "warning"),
    [
        (
            ProjectSuggestionError("Clé Jev refusée."),
            "Suggestions Jev interrompues : Clé Jev refusée.",
        ),
        (KeyError("server text"), "Suggestions Jev interrompues : erreur inattendue."),
    ],
)
def test_jev_failure_stops_suggestions_without_changing_found_numbers(
    error: Exception, warning: str,
) -> None:
    mailbox = jev_mailbox()
    mailbox.items["inbox-folder"].append(mail_item("D", subject="Question"))
    suggester = FakeSuggester(error=error)

    analysis = MailboxSortService(mailbox).analyze(
        request(), suggester=suggester, history=jev_history(),
    )

    assert len(suggester.calls) == 1
    assert analysis.warnings == [warning]
    assert by_id(analysis)["C"].status == SortStatus.READY
    assert by_id(analysis)["D"].status == SortStatus.NO_NUMBER


def controller_with(mailbox: FakeMailbox, directory: Any = None) -> AppController:
    return AppController(
        scan_service=cast(Any, SimpleNamespace()),
        preview_pipeline=cast(Any, SimpleNamespace()),
        projects_root=Path("projets"),
        report_dir=Path("rapports"),
        directory_store=directory,
        mailbox_service=MailboxSortService(mailbox),
    )


def test_controller_sorts_and_removes_sorted_mails_from_the_list() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [mail_item("A", subject="2025-4893"), mail_item("B")],
    })
    controller = controller_with(mailbox)

    analysis = controller.analyze_mailbox(
        MailboxSortRequest(account_identifier="  ", outlook_root_folder=" Boite de reception ")
    )
    result = controller.sort_mailbox({"A": ("2025-4893",)})
    controller.open_mailbox_mail("B")

    assert result.moved_count == 1
    assert [proposal.entry_id for proposal in analysis.proposals] == ["B"]
    assert set(analysis.items) == {"B"}
    assert mailbox.calls[-1] == ("display", "B", "")
    with pytest.raises(LookupError):
        controller.open_mailbox_mail("A")


def test_controller_uses_the_directory_for_jev_candidates() -> None:
    suggester = FakeSuggester(ProjectSuggestion("2025-5012", 0.8))
    controller = controller_with(jev_mailbox(), directory=jev_history())

    analysis = controller.analyze_mailbox(request(), suggester=suggester)

    assert by_id(analysis)["A"].status == SortStatus.SUGGESTED


def test_controller_requires_an_analysis_and_a_root_folder() -> None:
    controller = controller_with(FakeMailbox({}))

    with pytest.raises(RuntimeError):
        controller.sort_mailbox({"A": ("2025-4893",)})
    with pytest.raises(ValueError):
        controller.analyze_mailbox(
            MailboxSortRequest(account_identifier=None, outlook_root_folder=" ")
        )


def test_folders_created_meanwhile_turn_missing_mails_ready() -> None:
    mailbox = FakeMailbox({
        "inbox-folder": [
            mail_item("A", subject="Nouveau projet 2026-0100"),
            mail_item("B", subject="2025-4893 et 2026-0100"),
            mail_item("C", subject="2026-0200"),
            mail_item("D", subject="Brouillon", unreadable=True),
            mail_item("E"),
        ],
    })
    controller = controller_with(mailbox)
    analysis = controller.analyze_mailbox(request())
    assert missing_project_numbers(analysis) == ["2026-0100", "2026-0200"]

    mailbox.folders["2026-0100"] = ProjectFolder(
        "2026-0100", "2026-0100 (Halle)", "Inbox/2026/2026-0100", "2026-0100",
    )
    refreshed = controller.refresh_mailbox_project_folders()

    proposals = by_id(refreshed)
    assert refreshed is analysis
    assert proposals["A"].status == SortStatus.READY
    assert proposals["A"].destinations == ("2026-0100",)
    assert proposals["A"].selected_by_default
    assert proposals["B"].destinations == ("2025-4893", "2026-0100")
    assert proposals["B"].note == ""
    assert proposals["C"].status == SortStatus.MISSING_FOLDER
    assert "ProjectFlow" in proposals["C"].note
    assert proposals["D"].status == SortStatus.UNREADABLE
    assert proposals["E"].status == SortStatus.NO_NUMBER
    assert missing_project_numbers(refreshed) == ["2026-0200"]


def test_project_folders_cannot_be_refreshed_before_an_analysis() -> None:
    controller = controller_with(FakeMailbox({}))

    with pytest.raises(RuntimeError):
        controller.refresh_mailbox_project_folders()
    with pytest.raises(RuntimeError):
        MailboxSortService(FakeMailbox({})).refresh_project_folders(MailboxAnalysis())


def test_replanning_keeps_proposals_without_numbers() -> None:
    suggested = SortProposal(
        entry_id="A", source=MailboxSourceKind.INBOX, source_label="Inbox", subject="",
        correspondent="", sent_at=None, direction=Direction.RECEIVED,
        status=SortStatus.SUGGESTED, suggestion=ProjectSuggestion("2025-4893", 0.9),
    )

    assert replan_proposal(suggested, {}) is suggested
