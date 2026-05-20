from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.project_html_exporter import export_project_correspondence_html
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
from mailflow.outlook.attachments import PR_ATTACH_CONTENT_ID, PR_ATTACH_MIME_TAG


class FakePropertyAccessor:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def GetProperty(self, schema: str) -> object:
        if schema not in self.values:
            raise RuntimeError("missing property")
        return self.values[schema]


class FakeAttachment:
    def __init__(
        self,
        filename: str,
        content: str,
        *,
        properties: dict[str, object] | None = None,
    ) -> None:
        self.FileName = filename
        self.content = content
        self.PropertyAccessor = FakePropertyAccessor(properties)

    def SaveAsFile(self, path: str) -> None:
        Path(path).write_text(self.content, encoding="utf-8")


class FakeCollection:
    def __init__(self, items: list[FakeAttachment]) -> None:
        self.items = items
        self.Count = len(items)

    def Item(self, index: int) -> object:
        return self.items[index - 1]


class FakeMailItem:
    def __init__(self, attachments: list[FakeAttachment]) -> None:
        self.Attachments = FakeCollection(attachments)


def make_row(
    tmp_path: Path,
    *,
    entry_id: str = "ENTRY-1",
    direction: Direction = Direction.RECEIVED,
    sent_at: datetime = datetime(2026, 5, 6, 10, 30),
) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=direction,
        subject="Offre garde-corps",
        sender_name="Dupont SA",
        sender_email="sales@dupont.test",
        recipients=["lionel@balzmetal.test"],
        sent_at=sent_at,
        attachment_names=["plan.pdf"],
        body_excerpt="Prix <special> et delai de livraison.",
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Correspondance",
        target_path=tmp_path,
        confidence=0.91,
        duplicate_status="none",
        reason="Offre detectee.",
    )
    return PreviewRow(
        mail=mail,
        classification=ClassificationResult(
            rule=RuleClassification(
                suggested_type=MailType.DEVIS,
                suggested_interlocutor=InterlocutorType.FOURNISSEUR,
                likely_archive=True,
                confidence=0.91,
                matched_rules=["devis"],
                matched_terms=["Offre"],
            )
        ),
        decision=decision,
        action=PreviewAction.ARCHIVE,
    )


def create_project_folder(projects_root: Path) -> Path:
    project_path = projects_root / "2025" / "2025-4893"
    project_path.mkdir(parents=True)
    return project_path


def test_project_html_export_writes_single_html_and_shared_attachments(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)
    item = FakeMailItem([FakeAttachment("plan.pdf", "pdf")])

    results = export_project_correspondence_html(
        [row],
        {row.mail.entry_id: item},
        tmp_path,
    )

    assert len(results) == 1
    result = results[0]
    assert result.html_path == (
        tmp_path
        / "2025"
        / "2025-4893"
        / "Correspondance"
        / "2025-4893 - Correspondance projet.html"
    )
    assert result.html_path.exists()
    assert result.mail_count == 1
    assert result.attachment_paths == [
        result.attachment_dir / "1-R-Offre garde-corps - plan.pdf"
    ]
    assert result.attachment_paths[0].read_text(encoding="utf-8") == "pdf"
    html = result.html_path.read_text(encoding="utf-8")
    assert "Prix &lt;special&gt;" in html
    assert 'data-direction="received"' in html
    assert 'id="folderFilter"' in html
    assert 'data-folder="Correspondance"' in html
    assert '<span class="chip">Correspondance</span>' in html
    assert "./2025-4893%20-%20pieces%20jointes/1-R-Offre%20garde-corps%20-%20plan.pdf" in html
    assert 'data-attachment-link' in html
    assert 'data-local-href="file:///' in html
    assert 'target="_blank"' in html
    assert not list(result.html_path.parent.glob("*.msg"))
    assert not list(result.attachment_dir.glob("*pieces jointes*"))


def test_project_html_export_orders_sent_and_received_chronologically(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    sent = make_row(
        tmp_path,
        entry_id="ENTRY-2",
        direction=Direction.SENT,
        sent_at=datetime(2026, 5, 6, 12, 0),
    )
    received = make_row(
        tmp_path,
        entry_id="ENTRY-1",
        direction=Direction.RECEIVED,
        sent_at=datetime(2026, 5, 6, 10, 0),
    )

    result = export_project_correspondence_html(
        [sent, received],
        {
            sent.mail.entry_id: FakeMailItem([]),
            received.mail.entry_id: FakeMailItem([]),
        },
        tmp_path,
    )[0]

    html = result.html_path.read_text(encoding="utf-8")
    assert html.index(">1-R<") < html.index(">2-E<")


def test_project_html_export_renders_folder_tree_and_groups_mails(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    request = make_row(tmp_path, entry_id="ENTRY-1").model_copy(
        update={
            "decision": make_row(tmp_path).decision.model_copy(
                update={"target_relative_folder": "Fournisseurs/Demande de prix/Metal Factory"}
            )
        }
    )
    order = make_row(tmp_path, entry_id="ENTRY-2").model_copy(
        update={
            "decision": make_row(tmp_path).decision.model_copy(
                update={"target_relative_folder": "Fournisseurs/Commande/Metal Factory"}
            )
        }
    )

    result = export_project_correspondence_html(
        [request, order],
        {
            request.mail.entry_id: FakeMailItem([]),
            order.mail.entry_id: FakeMailItem([]),
        },
        tmp_path,
    )[0]

    html = result.html_path.read_text(encoding="utf-8")
    assert 'class="folder-panel"' in html
    assert 'data-folder-filter="Fournisseurs"' in html
    assert 'data-folder-section="Fournisseurs/Demande de prix/Metal Factory"' in html
    assert "<h2>Fournisseurs/Commande/Metal Factory</h2>" in html
    assert html.index("Fournisseurs/Demande de prix/Metal Factory") < html.index(
        "Fournisseurs/Commande/Metal Factory"
    )
    assert "matchesFolder(card.dataset.folder, activeFolder)" in html


def test_project_html_export_refuses_existing_html_until_confirmed(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)
    first_item = FakeMailItem([FakeAttachment("plan.pdf", "original")])
    second_item = FakeMailItem([FakeAttachment("plan.pdf", "updated")])

    first_result = export_project_correspondence_html(
        [row],
        {row.mail.entry_id: first_item},
        tmp_path,
    )[0]

    with pytest.raises(FileExistsError):
        export_project_correspondence_html(
            [row],
            {row.mail.entry_id: second_item},
            tmp_path,
        )

    second_result = export_project_correspondence_html(
        [row],
        {row.mail.entry_id: second_item},
        tmp_path,
        overwrite_html=True,
    )[0]

    assert second_result.html_path == first_result.html_path
    assert second_result.attachment_paths[0].read_text(encoding="utf-8") == "original"


def test_project_html_export_refuses_missing_project_folder(tmp_path: Path) -> None:
    row = make_row(tmp_path)

    with pytest.raises(FileNotFoundError):
        export_project_correspondence_html(
            [row],
            {row.mail.entry_id: FakeMailItem([])},
            tmp_path,
        )


def test_project_html_export_keeps_unavailable_attachments_visible(tmp_path: Path) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)

    result = export_project_correspondence_html([row], {}, tmp_path)[0]

    html = result.html_path.read_text(encoding="utf-8")
    assert "plan.pdf (non exportee)" in html
    assert result.attachment_paths == []


def test_project_html_export_embeds_inline_images_without_listing_them_as_attachments(
    tmp_path: Path,
) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)
    inline_image = FakeAttachment(
        "logo.png",
        "image",
        properties={
            PR_ATTACH_CONTENT_ID: "cid-logo",
            PR_ATTACH_MIME_TAG: "image/png",
        },
    )
    regular_attachment = FakeAttachment("plan.pdf", "pdf")

    result = export_project_correspondence_html(
        [row],
        {row.mail.entry_id: FakeMailItem([inline_image, regular_attachment])},
        tmp_path,
    )[0]

    html = result.html_path.read_text(encoding="utf-8")
    assert 'class="inline-mail-image"' in html
    assert "data:image/png;base64,aW1hZ2U=" in html
    assert ">logo.png<" not in html
    assert [path.name for path in result.attachment_paths] == [
        "1-R-Offre garde-corps - plan.pdf"
    ]
    assert not list(result.attachment_dir.glob("*logo*"))


def test_project_html_export_does_not_create_attachment_folder_for_inline_images_only(
    tmp_path: Path,
) -> None:
    create_project_folder(tmp_path)
    row = make_row(tmp_path)
    inline_image = FakeAttachment(
        "logo.png",
        "image",
        properties={
            PR_ATTACH_CONTENT_ID: "cid-logo",
            PR_ATTACH_MIME_TAG: "image/png",
        },
    )

    result = export_project_correspondence_html(
        [row],
        {row.mail.entry_id: FakeMailItem([inline_image])},
        tmp_path,
    )[0]

    assert "inline-mail-image" in result.html_path.read_text(encoding="utf-8")
    assert result.attachment_paths == []
    assert not result.attachment_dir.exists()
