from __future__ import annotations

from pathlib import Path

from mailflow.core.filenames import (
    build_attachment_filename,
    build_msg_filename,
    next_archive_order,
    sanitize_windows_filename,
    short_hash,
    suffix_copy_name,
)
from mailflow.models import Direction


def test_sanitize_windows_filename_removes_forbidden_characters() -> None:
    value = sanitize_windows_filename('Offre <garde-corps> : "A/B\\C"|?*')

    assert "<" not in value
    assert ">" not in value
    assert ":" not in value
    assert "/" not in value
    assert "\\" not in value


def test_sanitize_windows_filename_limits_length() -> None:
    assert len(sanitize_windows_filename("x" * 300, max_length=180)) == 180


def test_short_hash_is_stable_uppercase() -> None:
    assert short_hash("entry", "subject") == short_hash("entry", "subject")
    assert short_hash("entry", "subject").isupper()


def test_build_msg_filename_uses_order_direction_and_subject() -> None:
    filename = build_msg_filename(
        1,
        Direction.SENT,
        "Offre garde-corps",
    )

    assert filename == "1-E-Offre garde-corps.msg"


def test_build_attachment_filename_uses_mail_stem() -> None:
    filename = build_attachment_filename("2-R-Commande", "plan.pdf")

    assert filename == "2-R-Commande - plan.pdf"


def test_next_archive_order_continues_existing_prefixes(tmp_path: Path) -> None:
    (tmp_path / "1-E-Offre.msg").write_text("msg", encoding="utf-8")
    (tmp_path / "2-R-Offre - plan.pdf").write_text("pdf", encoding="utf-8")

    assert next_archive_order(tmp_path) == 3


def test_suffix_copy_name() -> None:
    assert suffix_copy_name("plan.pdf", 2) == "plan (2).pdf"
