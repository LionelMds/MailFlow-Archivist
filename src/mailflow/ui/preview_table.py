from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mailflow.core.manual_review import MANUAL_DESTINATIONS
from mailflow.models import InterlocutorType, MailType, PreviewAction, PreviewRow

PREVIEW_COLUMNS = (
    "Projet",
    "Date",
    "Sens",
    "Expediteur",
    "Sujet",
    "Type detecte",
    "Interlocuteur",
    "Destination proposee",
    "Confiance",
    "Action",
)

ACTION_LABELS = {
    PreviewAction.ARCHIVE: "Archiver",
    PreviewAction.ARCHIVED: "Archive",
    PreviewAction.IGNORE: "Ignorer",
    PreviewAction.REVIEW: "A verifier",
}

MAIL_TYPE_OPTIONS = tuple(mail_type.value for mail_type in MailType)
INTERLOCUTOR_OPTIONS = tuple(interlocutor.value for interlocutor in InterlocutorType)
DESTINATION_OPTIONS = MANUAL_DESTINATIONS

TYPE_COLUMN = PREVIEW_COLUMNS.index("Type detecte")
INTERLOCUTOR_COLUMN = PREVIEW_COLUMNS.index("Interlocuteur")
DESTINATION_COLUMN = PREVIEW_COLUMNS.index("Destination proposee")
REVIEW_EDITABLE_COLUMNS = {TYPE_COLUMN, INTERLOCUTOR_COLUMN, DESTINATION_COLUMN}


def editable_options_for_column(column: int) -> tuple[str, ...] | None:
    if column == TYPE_COLUMN:
        return MAIL_TYPE_OPTIONS
    if column == INTERLOCUTOR_COLUMN:
        return INTERLOCUTOR_OPTIONS
    if column == DESTINATION_COLUMN:
        return DESTINATION_OPTIONS
    return None


def row_requires_manual_attention(row: PreviewRow) -> bool:
    decision = row.decision
    return (
        row.action == PreviewAction.REVIEW
        or decision.requires_review
        or decision.mail_type == MailType.A_VERIFIER
        or decision.interlocutor == InterlocutorType.INCONNU
        or decision.target_relative_folder in {"A verifier", "Ne pas archiver"}
    )


def should_highlight_cell(row: PreviewRow, column: int) -> bool:
    return row_requires_manual_attention(row) and column in REVIEW_EDITABLE_COLUMNS


def preview_row_to_cells(row: PreviewRow) -> list[str]:
    mail = row.mail
    decision = row.decision
    return [
        mail.project_number,
        mail.sent_at.strftime("%Y-%m-%d %H:%M"),
        "Envoye" if mail.direction.value == "sent" else "Recu",
        mail.sender_name or mail.sender_email,
        mail.subject,
        decision.mail_type.value,
        decision.interlocutor.value,
        decision.target_relative_folder,
        f"{decision.confidence:.0%}",
        ACTION_LABELS[row.action],
    ]


class PreviewTableProtocol(Protocol):
    def setColumnCount(self, columns: int) -> None:
        ...

    def setRowCount(self, rows: int) -> None:
        ...

    def setHorizontalHeaderLabels(self, labels: list[str]) -> None:
        ...

    def setItem(self, row: int, column: int, item: object) -> None:
        ...


def preview_rows_to_matrix(rows: list[PreviewRow]) -> list[list[str]]:
    return [preview_row_to_cells(row) for row in rows]


def render_preview_rows(
    table: PreviewTableProtocol,
    rows: list[PreviewRow],
    *,
    item_factory: Callable[[str], object],
) -> None:
    table.setColumnCount(len(PREVIEW_COLUMNS))
    table.setRowCount(len(rows))
    table.setHorizontalHeaderLabels(list(PREVIEW_COLUMNS))
    for row_index, cells in enumerate(preview_rows_to_matrix(rows)):
        for column_index, value in enumerate(cells):
            table.setItem(row_index, column_index, item_factory(value))
