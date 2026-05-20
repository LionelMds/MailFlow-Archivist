from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mailflow.config import AppSettings
from mailflow.models import (
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    OutlookAccount,
    PreviewAction,
    PreviewRow,
    RuleClassification,
)
from mailflow.ui.main_window import (
    ARCHIVE_UI_DISABLED_MESSAGE,
    UI_TEXT,
    build_archive_confirmation_message,
    build_manual_classification_update,
    format_outlook_account_label,
    summarize_archive_selection,
)
from mailflow.ui.preview_table import ACTION_LABELS, PREVIEW_COLUMNS


class FakeController:
    def __init__(self) -> None:
        self.preview_rows: list[object] = []
        self.report_path = Path("rapport.csv")

    def scan_and_preview(self, _request: object) -> list[object]:
        self.preview_rows = []
        return []

    def export_report(self) -> Path:
        return self.report_path

    def mark_all_ignored(self) -> list[object]:
        self.preview_rows = []
        return []

    def rows_ready_for_archive(self, *, include_review: bool = False) -> list[object]:
        return []

    def suggested_account_identifier(self) -> str | None:
        return None

    def available_outlook_accounts(self) -> list[OutlookAccount]:
        return []

    def available_outlook_root_folders(
        self,
        account_identifier: str | None = None,
    ) -> list[str]:
        return ["Boite de reception"]

    def archive_ready(self, *, include_review: bool = False) -> object:
        return type(
            "Result",
            (),
            {"exported_count": 0, "skipped_count": 0, "failure_count": 0},
        )()

    def archive_selected(
        self,
        row_indexes: list[int],
        *,
        include_review: bool = False,
    ) -> object:
        return self.archive_ready(include_review=include_review)


def make_preview_row(tmp_path: Path, action: PreviewAction) -> PreviewRow:
    mail = MailMetadata(
        entry_id=f"ENTRY-{action.value}",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
        body_excerpt="Merci pour votre offre.",
    )
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
                likely_archive=action == PreviewAction.ARCHIVE,
                confidence=0.9,
                matched_rules=["devis"],
                matched_terms=["offre"],
            )
        ),
        decision=decision,
        action=action,
    )


def test_ui_text_contains_expected_actions() -> None:
    assert UI_TEXT["scan_button"] == "Scanner Outlook"
    assert "Destination proposee" in PREVIEW_COLUMNS
    assert ACTION_LABELS[PreviewAction.REVIEW] == "A verifier"
    assert "Archivage reel desactive" in ARCHIVE_UI_DISABLED_MESSAGE


def test_outlook_account_label_includes_smtp_address() -> None:
    account = OutlookAccount(display_name="Balz", smtp_address="lionel@balzmetal.test")

    assert format_outlook_account_label(account) == "Balz <lionel@balzmetal.test>"


def test_summarize_archive_selection_counts_ready_and_skipped_rows(tmp_path: Path) -> None:
    rows = [
        make_preview_row(tmp_path, PreviewAction.ARCHIVE),
        make_preview_row(tmp_path, PreviewAction.REVIEW),
        make_preview_row(tmp_path, PreviewAction.ARCHIVED),
    ]

    summary = summarize_archive_selection(rows, [0, 1, 2])

    assert summary.selected_count == 3
    assert summary.ready_count == 1
    assert summary.skipped_count == 2
    assert summary.can_archive
    assert "1 mail(s)" in build_archive_confirmation_message(summary)


def test_build_manual_classification_update_from_dialog_values() -> None:
    update = build_manual_classification_update(
        mail_type_value="devis",
        interlocutor_value="fournisseur",
        destination_value="Fournisseurs/Demande de prix",
        learning_term="Offerte",
        misleading_term="newsletter",
        manual_required=False,
    )

    assert update.mail_type == MailType.DEVIS
    assert update.interlocutor == InterlocutorType.FOURNISSEUR
    assert update.target_relative_folder == "Fournisseurs/Demande de prix"
    assert update.learning_term == "Offerte"
    assert update.misleading_term == "newsletter"
    assert not update.manual_required


def test_build_manual_classification_update_can_mark_manual_required() -> None:
    update = build_manual_classification_update(
        mail_type_value="a_verifier",
        interlocutor_value="inconnu",
        destination_value="A verifier",
        learning_term=None,
        manual_required=True,
    )

    assert update.mail_type == MailType.A_VERIFIER
    assert update.interlocutor == InterlocutorType.INCONNU
    assert update.learning_term is None
    assert update.manual_required


def test_main_window_instantiates_when_pyside6_is_available() -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(AppSettings(), controller=FakeController())
    dynamic_window = cast(Any, window)

    assert window.windowTitle() == "MailFlow Archivist"
    assert dynamic_window.mailflow_outlook_root_combo.currentText() == "Boite de reception"
    assert dynamic_window.mailflow_mail_preview.isReadOnly()
    window.close()
    app.quit()
