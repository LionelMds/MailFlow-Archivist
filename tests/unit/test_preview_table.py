from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
from mailflow.ui.preview_table import (
    DESTINATION_COLUMN,
    DESTINATION_OPTIONS,
    INTERLOCUTOR_COLUMN,
    INTERLOCUTOR_OPTIONS,
    MAIL_TYPE_OPTIONS,
    PREVIEW_COLUMNS,
    TYPE_COLUMN,
    editable_options_for_column,
    preview_rows_to_matrix,
    render_preview_rows,
    should_highlight_cell,
)


class FakeTable:
    def __init__(self) -> None:
        self.column_count = 0
        self.row_count = 0
        self.headers: list[str] = []
        self.items: dict[tuple[int, int], object] = {}

    def setColumnCount(self, columns: int) -> None:
        self.column_count = columns

    def setRowCount(self, rows: int) -> None:
        self.row_count = rows

    def setHorizontalHeaderLabels(self, labels: list[str]) -> None:
        self.headers = labels

    def setItem(self, row: int, column: int, item: object) -> None:
        self.items[(row, column)] = item


def make_row(tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
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
        action=PreviewAction.ARCHIVE,
    )


def make_review_row(tmp_path: Path) -> PreviewRow:
    row = make_row(tmp_path)
    decision = row.decision.model_copy(
        update={
            "mail_type": MailType.A_VERIFIER,
            "interlocutor": InterlocutorType.INCONNU,
            "target_relative_folder": "A verifier",
            "archive": False,
            "requires_review": True,
        }
    )
    return row.model_copy(update={"decision": decision, "action": PreviewAction.REVIEW})


def test_preview_rows_to_matrix_formats_cells(tmp_path: Path) -> None:
    matrix = preview_rows_to_matrix([make_row(tmp_path)])

    assert matrix[0] == [
        "2025-4893",
        "2026-05-06 10:30",
        "Recu",
        "Dupont",
        "Offre",
        "demande_de_prix",
        "fournisseur",
        "Fournisseurs/Demande de prix",
        "90%",
        "Archiver",
    ]


def test_render_preview_rows_sets_headers_and_items(tmp_path: Path) -> None:
    table = FakeTable()

    render_preview_rows(table, [make_row(tmp_path)], item_factory=lambda value: f"item:{value}")

    assert table.column_count == len(PREVIEW_COLUMNS)
    assert table.row_count == 1
    assert table.headers == list(PREVIEW_COLUMNS)
    assert table.items[(0, 0)] == "item:2025-4893"


def test_editable_options_for_manual_columns() -> None:
    assert "devis" in MAIL_TYPE_OPTIONS
    assert "facture" not in MAIL_TYPE_OPTIONS
    assert "fournisseur" in INTERLOCUTOR_OPTIONS
    assert "Fournisseurs/Demande de prix" in DESTINATION_OPTIONS
    assert editable_options_for_column(TYPE_COLUMN) == MAIL_TYPE_OPTIONS
    assert editable_options_for_column(INTERLOCUTOR_COLUMN) == INTERLOCUTOR_OPTIONS
    assert editable_options_for_column(DESTINATION_COLUMN) == DESTINATION_OPTIONS
    assert editable_options_for_column(0) is None


def test_review_rows_highlight_manual_cells_only(tmp_path: Path) -> None:
    row = make_review_row(tmp_path)

    assert should_highlight_cell(row, TYPE_COLUMN)
    assert should_highlight_cell(row, INTERLOCUTOR_COLUMN)
    assert should_highlight_cell(row, DESTINATION_COLUMN)
    assert not should_highlight_cell(row, 0)
