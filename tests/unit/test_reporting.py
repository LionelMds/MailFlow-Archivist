from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from mailflow.core.reporting import export_preview_report, preview_row_to_report_record
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


def make_row(tmp_path: Path) -> PreviewRow:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Dupont SA",
        sender_email="sales@dupont.test",
        recipients=["lionel@balzmetal.test"],
        sent_at=datetime(2026, 5, 6, 10, 30),
        body_excerpt="Ce corps ne doit pas etre exporte.",
    )
    decision = ArchiveDecision(
        mail_id="ENTRY-1",
        project_number="2025-4893",
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder="Fournisseurs/Demande de prix",
        target_path=tmp_path,
        confidence=0.9,
        duplicate_status="none",
        reason="Decision issue des regles locales.",
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


def test_preview_row_to_report_record_excludes_body(tmp_path: Path) -> None:
    record = preview_row_to_report_record(make_row(tmp_path))

    assert "body" not in record
    assert "Ce corps" not in ";".join(record.values())
    assert record["confidence"] == "0.90"


def test_export_preview_report_writes_semicolon_csv(tmp_path: Path) -> None:
    path = export_preview_report([make_row(tmp_path)], tmp_path / "rapport.csv")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))

    assert rows[0]["project"] == "2025-4893"
    assert rows[0]["action"] == "archive"

