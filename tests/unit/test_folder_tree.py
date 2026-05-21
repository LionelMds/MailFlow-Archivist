from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from mailflow.core.folder_tree import (
    build_folder_tree,
    folder_path_counts,
    merge_folder,
    rename_folder_leaf,
)
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


def make_row(tmp_path: Path, entry_id: str, folder: str) -> PreviewRow:
    mail = MailMetadata(
        entry_id=entry_id,
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.RECEIVED,
        subject="Offre",
        sender_name="Metal Factory",
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    decision = ArchiveDecision(
        mail_id=mail.entry_id,
        project_number=mail.project_number,
        archive=True,
        requires_review=False,
        mail_type=MailType.DEVIS,
        interlocutor=InterlocutorType.FOURNISSEUR,
        target_relative_folder=folder,
        target_path=tmp_path / "2025" / "2025-4893" / folder,
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


def test_build_folder_tree_counts_nested_folders(tmp_path: Path) -> None:
    rows = [
        make_row(tmp_path, "ENTRY-1", "DEMANDE DE PRIX/Metal Factory"),
        make_row(tmp_path, "ENTRY-2", "DEMANDE DE PRIX/Metal Factory"),
        make_row(tmp_path, "ENTRY-3", "COMMANDE/Metal Factory"),
    ]

    tree = build_folder_tree(rows)

    assert tree[0].name == "DEMANDE DE PRIX"
    assert tree[0].mail_count == 2
    assert tree[1].name == "COMMANDE"
    assert tree[0].children[0].mail_count == 2
    assert folder_path_counts(rows)[0].mail_count == 1


def test_rename_folder_leaf_updates_rows_and_target_paths(tmp_path: Path) -> None:
    rows = [
        make_row(tmp_path, "ENTRY-1", "DEMANDE DE PRIX/METAL-FACTORY"),
        make_row(tmp_path, "ENTRY-2", "COMMANDE/Metal Factory"),
    ]

    updated = rename_folder_leaf(
        rows,
        "DEMANDE DE PRIX/METAL-FACTORY",
        "Metal Factory",
        projects_root=tmp_path,
    )

    assert updated[0].decision.target_relative_folder == (
        "DEMANDE DE PRIX/Metal Factory"
    )
    assert updated[0].decision.target_path == (
        tmp_path
        / "2025"
        / "2025-4893"
        / "DEMANDE DE PRIX"
        / "Metal Factory"
    )
    assert updated[1] == rows[1]


def test_merge_folder_rewrites_source_prefix(tmp_path: Path) -> None:
    rows = [
        make_row(tmp_path, "ENTRY-1", "DEMANDE DE PRIX/METAL-FACTORY"),
        make_row(tmp_path, "ENTRY-2", "DEMANDE DE PRIX/Metal Factory"),
    ]

    updated = merge_folder(
        rows,
        "DEMANDE DE PRIX/METAL-FACTORY",
        "DEMANDE DE PRIX/Metal Factory",
        projects_root=tmp_path,
    )

    assert [row.decision.target_relative_folder for row in updated] == [
        "DEMANDE DE PRIX/Metal Factory",
        "DEMANDE DE PRIX/Metal Factory",
    ]
    assert "fusionne" in updated[0].decision.reason


def test_folder_rewrite_rejects_unsafe_or_self_targets(tmp_path: Path) -> None:
    rows = [make_row(tmp_path, "ENTRY-1", "DEMANDE DE PRIX/Metal Factory")]

    with pytest.raises(ValueError):
        rename_folder_leaf(
            rows,
            "DEMANDE DE PRIX/Metal Factory",
            "Metal Factory",
            projects_root=tmp_path,
        )
    with pytest.raises(ValueError):
        merge_folder(
            rows,
            "DEMANDE DE PRIX/Metal Factory",
            "C:/Foo",
            projects_root=tmp_path,
        )
    with pytest.raises(ValueError):
        merge_folder(
            rows,
            "DEMANDE DE PRIX/Metal Factory",
            "DEMANDE DE PRIX/Metal Factory/Archive",
            projects_root=tmp_path,
        )
