from __future__ import annotations

import csv
from pathlib import Path

from mailflow.models import PreviewRow

REPORT_COLUMNS = (
    "project",
    "sent_at",
    "direction",
    "sender",
    "subject",
    "mail_type",
    "interlocutor",
    "target_folder",
    "confidence",
    "action",
    "duplicate_status",
    "reason",
)


def export_preview_report(rows: list[PreviewRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow(preview_row_to_report_record(row))
    return path


def preview_row_to_report_record(row: PreviewRow) -> dict[str, str]:
    mail = row.mail
    decision = row.decision
    return {
        "project": mail.project_number,
        "sent_at": mail.sent_at.isoformat(),
        "direction": mail.direction.value,
        "sender": _text_cell(mail.sender_email or mail.sender_name),
        "subject": _text_cell(mail.subject),
        "mail_type": decision.mail_type.value,
        "interlocutor": decision.interlocutor.value,
        "target_folder": _text_cell(decision.target_relative_folder),
        "confidence": f"{decision.confidence:.2f}",
        "action": row.action.value,
        "duplicate_status": decision.duplicate_status,
        "reason": _text_cell(decision.reason),
    }


def _text_cell(value: str) -> str:
    """Keep a text that comes from a mail from being run as a formula by Excel."""
    return f"'{value}" if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
