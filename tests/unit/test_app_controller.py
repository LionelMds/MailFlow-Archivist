from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.app_controller import AppController, PreviewRequest, selected_rows
from mailflow.core.archive_batch import ArchiveBatchExecutor
from mailflow.core.contact_directory import (
    ContactObservation,
    DirectoryUpsertOutcome,
    OrganizationDirectoryEntry,
    ProjectParticipantEntry,
)
from mailflow.core.scan_service import DirectoryScanRequest, ScanRequest
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    ManualClassificationUpdate,
    ManualLearningSignal,
    PreviewAction,
    PreviewRow,
    RuleClassification,
    VerifiedRoutingExample,
)
from mailflow.outlook.exporter import ExportResult
from mailflow.outlook.scanner import ScannedMail


class FakeScanService:
    def __init__(self, mails: list[MailMetadata]) -> None:
        self.mails = mails
        self.requests: list[ScanRequest] = []
        self.directory_requests: list[DirectoryScanRequest] = []

    def scan(self, request: ScanRequest) -> list[MailMetadata]:
        return [scanned.metadata for scanned in self.scan_with_items(request)]

    def scan_with_items(self, request: ScanRequest) -> list[ScannedMail]:
        self.requests.append(request)
        return [ScannedMail(item=object(), metadata=mail) for mail in self.mails]

    def scan_all_project_folders_with_items(
        self,
        request: DirectoryScanRequest,
    ) -> list[ScannedMail]:
        self.directory_requests.append(request)
        return [ScannedMail(item=object(), metadata=mail) for mail in self.mails]


class FakeArchiveService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def archive(
        self,
        item: object,
        metadata: MailMetadata,
        decision: ArchiveDecision,
    ) -> ExportResult:
        self.calls.append(metadata.entry_id)
        return ExportResult(msg_path=decision.target_path / "mail.msg", attachment_paths=[])


class FakeAttachment:
    FileName = "plan.pdf"

    def SaveAsFile(self, path: str) -> None:
        Path(path).write_text("pdf", encoding="utf-8")


class FakeAttachmentCollection:
    Count = 1

    def Item(self, _index: int) -> object:
        return FakeAttachment()


class FakeMailItem:
    Attachments = FakeAttachmentCollection()


class FakeLearningStore:
    def __init__(self) -> None:
        self.signals: list[ManualLearningSignal] = []

    def record(self, signal: ManualLearningSignal) -> None:
        self.signals.append(signal)

    def verified_examples(self) -> list[VerifiedRoutingExample]:
        return []


class FakeDirectoryStore:
    def __init__(self) -> None:
        self.contacts: list[str] = []
        self.domain_map: dict[str, str] = {}
        self.entries = [
            OrganizationDirectoryEntry(
                organization_id=1,
                name="AIG",
                domains=("gva.ch",),
                contacts=("contact@gva.ch",),
                project_count=1,
            )
        ]
        self.renamed: tuple[int, str] | None = None
        self.merged: tuple[int, int] | None = None
        self.roles: dict[tuple[str, int], InterlocutorType] = {}

    def record_observation(self, observation: ContactObservation) -> DirectoryUpsertOutcome:
        self.contacts.append(observation.email)
        self.domain_map[observation.domain] = observation.organization_name
        return DirectoryUpsertOutcome(
            new_organization=True,
            new_domain=observation.allow_domain_mapping,
            new_contact=True,
            new_project_participant=True,
        )

    def organization_name_for_email(self, email: str) -> str | None:
        domain = email.rsplit("@", maxsplit=1)[-1].casefold()
        return self.domain_map.get(domain)

    def organization_id_for_email(self, email: str) -> int | None:
        return 1 if self.organization_name_for_email(email) else None

    def list_organizations(self) -> list[OrganizationDirectoryEntry]:
        return self.entries

    def rename_organization(self, organization_id: int, name: str) -> None:
        self.renamed = (organization_id, name)

    def merge_organizations(
        self,
        source_organization_id: int,
        target_organization_id: int,
    ) -> None:
        self.merged = (source_organization_id, target_organization_id)

    def list_project_participants(self, project_number: str) -> list[ProjectParticipantEntry]:
        return [
            ProjectParticipantEntry(
                organization_id=entry.organization_id,
                name=entry.name,
                domains=entry.domains,
                contacts=entry.contacts,
                role=self.roles.get(
                    (project_number, entry.organization_id),
                    InterlocutorType.INCONNU,
                ),
                mail_count=1,
            )
            for entry in self.entries
        ]

    def set_project_participant_role(
        self,
        project_number: str,
        organization_id: int,
        role: InterlocutorType,
    ) -> None:
        self.roles[(project_number, organization_id)] = role

    def interlocutor_for_email(
        self,
        project_number: str,
        email: str,
    ) -> InterlocutorType | None:
        domain = email.rsplit("@", maxsplit=1)[-1].casefold()
        if self.domain_map.get(domain) is None and domain != "gva.ch":
            return None
        return self.roles.get((project_number, 1))


class FakePreviewPipeline:
    def __init__(self, rows: list[PreviewRow]) -> None:
        self.rows = rows
        self.mails: list[MailMetadata] = []
        self.examples: list[VerifiedRoutingExample] = []

    def add_verified_example(self, example: VerifiedRoutingExample) -> None:
        self.examples.append(example)

    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PreviewRow]:
        self.mails = mails
        if progress_callback is not None:
            for index, _mail in enumerate(mails, start=1):
                progress_callback(index, len(mails))
        return self.rows


def make_mail(entry_id: str = "ENTRY-1") -> MailMetadata:
    return MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )


def create_project_folder(projects_root: Path) -> Path:
    path = projects_root / "2025" / "2025-4893"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_row(
    tmp_path: Path,
    action: PreviewAction = PreviewAction.ARCHIVE,
    entry_id: str = "ENTRY-1",
) -> PreviewRow:
    mail = make_mail(entry_id)
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=action == PreviewAction.ARCHIVE,
        requires_review=action == PreviewAction.REVIEW,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="ok",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=MailType.DEVIS,
                suggested_interlocutor=InterlocutorType.FOURNISSEUR,
                likely_archive=True,
                confidence=0.9,
                matched_rules=["devis"],
            )
        ),
        decision=decision,
        action=action,
    )


def test_controller_scans_and_builds_preview_rows(tmp_path: Path) -> None:
    mail = make_mail()
    row = make_row(tmp_path)
    scanner = FakeScanService([mail])
    pipeline = FakePreviewPipeline([row])
    controller = AppController(
        scan_service=scanner,
        preview_pipeline=pipeline,
        projects_root=tmp_path,
        report_dir=tmp_path,
    )

    rows = controller.scan_and_preview(
        PreviewRequest(
            account_identifier=" Balz ",
            outlook_root_folder=" Boite de reception ",
            year=" 2025 ",
            project_number=" 2025-4893 ",
        )
    )

    assert rows == [row]
    assert controller.preview_rows == [row]
    assert pipeline.mails == [mail]
    assert scanner.requests == [
        ScanRequest(
            account_identifier="Balz",
            outlook_root_folder="Boite de reception",
            year="2025",
            project_number="2025-4893",
        )
    ]


def test_controller_reports_scan_progress(tmp_path: Path) -> None:
    mail = make_mail()
    row = make_row(tmp_path)
    controller = AppController(
        scan_service=FakeScanService([mail]),
        preview_pipeline=FakePreviewPipeline([row]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    messages: list[str] = []

    controller.scan_and_preview(
        PreviewRequest(
            account_identifier="Balz",
            outlook_root_folder="Boite de reception",
            year="2025",
            project_number="4893",
        ),
        progress_callback=messages.append,
    )

    assert messages == [
        "Lecture Outlook en cours...",
        "1 mail(s) lus. Classification en cours...",
        "Classification 1/1...",
        "1 mail(s) prets.",
    ]


def test_controller_reset_preview_clears_rows_and_outlook_items(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [row]
    controller.outlook_items = {row.mail.entry_id: object()}

    rows = controller.reset_preview()

    assert rows == []
    assert controller.preview_rows == []
    assert controller.outlook_items == {}


def test_controller_reclassifies_loaded_mails_without_rescanning_outlook(tmp_path: Path) -> None:
    original = make_row(tmp_path, PreviewAction.REVIEW)
    reclassified = make_row(tmp_path, PreviewAction.ARCHIVE)
    pipeline = FakePreviewPipeline([reclassified])
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=pipeline,
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [original]
    messages: list[str] = []

    rows = controller.reclassify_preview(progress_callback=messages.append)

    assert rows == [reclassified]
    assert pipeline.mails == [original.mail]
    assert messages == ["Reclassification 1/1..."]


def test_controller_rejects_missing_year(tmp_path: Path) -> None:
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )

    with pytest.raises(ValueError):
        controller.scan_and_preview(
            PreviewRequest(
                account_identifier=None,
                outlook_root_folder="Boite de reception",
                year=" ",
            )
        )


def test_controller_exports_current_preview_report(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([row]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [row]

    path = controller.export_report(tmp_path / "rapport.csv")

    assert path.exists()
    assert "2025-4893" in path.read_text(encoding="utf-8-sig")


def test_controller_exports_project_html_from_current_preview(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [row]
    controller.outlook_items = {row.mail.entry_id: FakeMailItem()}

    results = controller.export_project_html()

    assert len(results) == 1
    assert results[0].mail_count == 1
    assert results[0].html_path.exists()
    assert results[0].attachment_paths[0].name == "1-R-Offre - plan.pdf"


def test_controller_exports_project_html_only_selected_rows(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    first = make_row(tmp_path, entry_id="ENTRY-1").model_copy(
        update={"mail": make_mail("ENTRY-1").model_copy(update={"subject": "Premier"})}
    )
    second = make_row(tmp_path, entry_id="ENTRY-2")
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [first, second]
    controller.outlook_items = {
        first.mail.entry_id: FakeMailItem(),
        second.mail.entry_id: FakeMailItem(),
    }

    results = controller.export_project_html([1])

    html = results[0].html_path.read_text(encoding="utf-8")
    assert results[0].mail_count == 1
    assert "Premier" not in html
    assert "Offre" in html


def test_controller_archive_selection_and_ignore(tmp_path: Path) -> None:
    row = make_row(tmp_path)
    second = make_row(tmp_path, entry_id="ENTRY-2")
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [row, second]

    assert controller.rows_ready_for_archive() == [row, second]
    ignored = controller.mark_selected_ignored([0])

    assert ignored[0].action == PreviewAction.IGNORE
    assert ignored[1].action == PreviewAction.ARCHIVE
    assert controller.rows_ready_for_archive() == [second]


def test_controller_can_restore_all_archivable_rows(tmp_path: Path) -> None:
    ignored = make_row(tmp_path, PreviewAction.IGNORE).model_copy(
        update={"decision": make_row(tmp_path).decision}
    )
    review = make_row(tmp_path, PreviewAction.REVIEW, "ENTRY-2")
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [ignored, review]

    restored = controller.mark_all_archivable()

    assert restored[0].action == PreviewAction.ARCHIVE
    assert restored[1].action == PreviewAction.REVIEW


def test_controller_restore_forces_ignored_rows_with_archive_false(tmp_path: Path) -> None:
    ignored = make_row(tmp_path, PreviewAction.IGNORE).model_copy(
        update={
            "decision": make_row(tmp_path).decision.model_copy(
                update={"archive": False, "requires_review": False}
            )
        }
    )
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [ignored]

    restored = controller.mark_all_archivable()

    assert restored[0].action == PreviewAction.ARCHIVE
    assert restored[0].decision.archive is True
    assert controller.rows_ready_for_archive() == restored


def test_controller_updates_preview_folder_tree(tmp_path: Path) -> None:
    row = make_row(tmp_path).model_copy(
        update={
            "decision": make_row(tmp_path).decision.model_copy(
                update={
                    "target_relative_folder": "Fournisseurs/Demande de prix/METAL-FACTORY",
                    "target_path": (
                        tmp_path
                        / "2025"
                        / "2025-4893"
                        / "Fournisseurs/Demande de prix"
                        / "METAL-FACTORY"
                    ),
                }
            )
        }
    )
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
    )
    controller.preview_rows = [row]

    tree = controller.folder_tree()
    assert tree[0].name == "Fournisseurs"
    assert tree[0].children[0].name == "Demande de prix"
    assert tree[0].children[0].children[0].name == "METAL-FACTORY"
    controller.rename_preview_folder(
        "Fournisseurs/Demande de prix/METAL-FACTORY",
        "Metal Factory",
    )

    assert controller.preview_rows[0].decision.target_relative_folder == (
        "Fournisseurs/Demande de prix/Metal Factory"
    )


def test_controller_imports_contact_directory_from_all_project_folders(tmp_path: Path) -> None:
    mail = make_mail().model_copy(
        update={
            "sender_name": "AIG",
            "sender_email": "contact@gva.ch",
            "recipients": ["lionel@balzmetal.ch"],
        }
    )
    scanner = FakeScanService([mail])
    directory_store = FakeDirectoryStore()
    controller = AppController(
        scan_service=scanner,
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=directory_store,
    )

    result = controller.import_contact_directory(
        account_identifier="lionel@balzmetal.ch",
        outlook_root_folder="Boite de reception",
    )

    assert scanner.directory_requests == [
        DirectoryScanRequest(
            account_identifier="lionel@balzmetal.ch",
            outlook_root_folder="Boite de reception",
        )
    ]
    assert result.imported_contact_count == 1
    assert directory_store.contacts == ["contact@gva.ch"]


def test_controller_exposes_directory_entries_and_edits(tmp_path: Path) -> None:
    directory_store = FakeDirectoryStore()
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=directory_store,
    )

    assert controller.directory_entries() == directory_store.entries
    controller.rename_directory_organization(1, "Aeroport International Geneve")
    controller.merge_directory_organizations(2, 1)

    assert directory_store.renamed == (1, "Aeroport International Geneve")
    assert directory_store.merged == (2, 1)


def test_controller_applies_project_role_to_all_preview_rows(tmp_path: Path) -> None:
    directory_store = FakeDirectoryStore()
    directory_store.domain_map["gva.ch"] = "AIG"
    row = make_row(tmp_path).model_copy(
        update={
            "mail": make_mail().model_copy(
                update={
                    "project_number": "2025-4893",
                    "direction": Direction.SENT,
                    "sender_email": "lionel@balzmetal.ch",
                    "recipients": ["chef@gva.ch", "andre@balzmetal.ch"],
                    "subject": "Plan pour approbation",
                }
            ),
            "decision": make_row(tmp_path).decision.model_copy(
                update={
                    "mail_type": MailType.PLAN,
                    "interlocutor": InterlocutorType.INTERNE,
                    "target_relative_folder": "Correspondance",
                    "target_path": tmp_path / "2025" / "2025-4893" / "Correspondance",
                }
            ),
        }
    )
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=directory_store,
    )
    controller.preview_rows = [row]

    updated = controller.set_project_participant_role(1, InterlocutorType.CLIENT)

    assert directory_store.roles[("2025-4893", 1)] == InterlocutorType.CLIENT
    assert updated[0].decision.interlocutor == InterlocutorType.CLIENT
    assert updated[0].decision.target_relative_folder == "Correspondance/AIG"


def test_controller_archives_ready_rows_with_stored_outlook_items(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    mail = make_mail()
    row = make_row(tmp_path)
    scanner = FakeScanService([mail])
    archive_service = FakeArchiveService()
    controller = AppController(
        scan_service=scanner,
        preview_pipeline=FakePreviewPipeline([row]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        archive_executor=ArchiveBatchExecutor(archive_service),
    )
    controller.scan_and_preview(
        PreviewRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    result = controller.archive_ready()

    assert result.exported_count == 1
    assert result.exported_mail_ids == ["ENTRY-1"]
    assert archive_service.calls == ["ENTRY-1"]
    assert controller.preview_rows[0].action == PreviewAction.ARCHIVED


def test_controller_creates_missing_destination_subfolders(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    mail = make_mail()
    row = make_row(tmp_path).model_copy(
        update={
            "decision": make_row(tmp_path).decision.model_copy(
                update={
                    "target_relative_folder": "Fournisseurs/Commande/Metal Factory",
                    "target_path": (
                        tmp_path
                        / "2025"
                        / "2025-4893"
                        / "Fournisseurs/Commande"
                        / "Metal Factory"
                    ),
                }
            )
        }
    )
    archive_service = FakeArchiveService()
    controller = AppController(
        scan_service=FakeScanService([mail]),
        preview_pipeline=FakePreviewPipeline([row]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        archive_executor=ArchiveBatchExecutor(archive_service),
    )
    controller.scan_and_preview(
        PreviewRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    result = controller.archive_ready()

    assert result.exported_count == 1
    assert row.decision.target_path.exists()


def test_controller_archives_only_selected_ready_rows(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    first_mail = make_mail("ENTRY-1")
    second_mail = make_mail("ENTRY-2")
    rows = [
        make_row(tmp_path, PreviewAction.ARCHIVE, "ENTRY-1"),
        make_row(tmp_path, PreviewAction.IGNORE, "ENTRY-2"),
    ]
    scanner = FakeScanService([first_mail, second_mail])
    archive_service = FakeArchiveService()
    controller = AppController(
        scan_service=scanner,
        preview_pipeline=FakePreviewPipeline(rows),
        projects_root=tmp_path,
        report_dir=tmp_path,
        archive_executor=ArchiveBatchExecutor(archive_service),
    )
    controller.scan_and_preview(
        PreviewRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    result = controller.archive_selected([0, 1])

    assert archive_service.calls == ["ENTRY-1"]
    assert result.exported_mail_ids == ["ENTRY-1"]
    assert result.skipped == ["ENTRY-2"]
    assert controller.preview_rows[0].action == PreviewAction.ARCHIVED
    assert controller.preview_rows[1].action == PreviewAction.IGNORE


def test_controller_refuses_archive_when_project_folder_is_missing(tmp_path: Path) -> None:
    projects_root = tmp_path / "missing-root"
    mail = make_mail()
    row = make_row(projects_root)
    scanner = FakeScanService([mail])
    archive_service = FakeArchiveService()
    controller = AppController(
        scan_service=scanner,
        preview_pipeline=FakePreviewPipeline([row]),
        projects_root=projects_root,
        report_dir=tmp_path,
        archive_executor=ArchiveBatchExecutor(archive_service),
    )
    controller.scan_and_preview(
        PreviewRequest(
            account_identifier=None,
            outlook_root_folder="Boite de reception",
            year="2025",
        )
    )

    result = controller.archive_selected([0])

    assert archive_service.calls == []
    assert result.exported_count == 0
    assert result.failure_count == 1
    assert "Dossier projet local absent" in result.failures[0].reason


def test_controller_applies_manual_update_and_records_verified_example(tmp_path: Path) -> None:
    row = make_row(tmp_path, PreviewAction.REVIEW)
    learning_store = FakeLearningStore()
    pipeline = FakePreviewPipeline([])
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=pipeline,
        projects_root=tmp_path,
        report_dir=tmp_path,
        learning_store=learning_store,
    )
    controller.preview_rows = [row]

    updated = controller.apply_manual_update(
        0,
        ManualClassificationUpdate(
            mail_type=MailType.DEMANDE_DE_PRIX,
            interlocutor=InterlocutorType.FOURNISSEUR,
            target_relative_folder="Fournisseurs/Demande de prix",
        ),
    )

    assert updated.action == PreviewAction.ARCHIVE
    assert controller.preview_rows[0].decision.mail_type == MailType.DEMANDE_DE_PRIX
    assert learning_store.signals[0].learning_term is None
    assert pipeline.examples[0].category.value == "Demande de prix"


def test_controller_manual_update_uses_directory_for_company_folder(tmp_path: Path) -> None:
    row = make_row(tmp_path, PreviewAction.REVIEW).model_copy(
        update={
            "mail": make_row(tmp_path, PreviewAction.REVIEW).mail.model_copy(
                update={
                    "sender_name": "Jean Dupont",
                    "sender_email": "jean.dupont@gva.ch",
                }
            )
        }
    )
    directory_store = FakeDirectoryStore()
    directory_store.domain_map["gva.ch"] = "AIG"
    controller = AppController(
        scan_service=FakeScanService([]),
        preview_pipeline=FakePreviewPipeline([]),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=directory_store,
    )
    controller.preview_rows = [row]

    updated = controller.apply_manual_update(
        0,
        ManualClassificationUpdate(
            mail_type=MailType.CORRESPONDANCE_GENERALE,
            interlocutor=InterlocutorType.CLIENT,
            target_relative_folder="Correspondance",
        ),
    )

    assert updated.decision.target_relative_folder == "Correspondance/AIG"


def test_selected_rows_ignores_invalid_indexes(tmp_path: Path) -> None:
    rows = [make_row(tmp_path), make_row(tmp_path, PreviewAction.IGNORE)]

    assert selected_rows(rows, [-1, 0, 99]) == [rows[0]]
