from __future__ import annotations

import hashlib
import re
from pathlib import Path

from mailflow.models import Direction

WINDOWS_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTISPACE_RE = re.compile(r"\s+")
ARCHIVE_PREFIX_RE = re.compile(r"^(?P<order>\d+)-[ER]-", re.IGNORECASE)


def sanitize_windows_filename(value: str, max_length: int = 180) -> str:
    cleaned = WINDOWS_FORBIDDEN_CHARS.sub("-", value)
    cleaned = MULTISPACE_RE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = "Sans sujet"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned


def short_hash(*parts: object, length: int = 6) -> str:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()[:length].upper()


def direction_prefix(direction: Direction) -> str:
    if direction == Direction.SENT:
        return "E"
    return "R"


def build_archive_stem(
    order: int,
    direction: Direction,
    subject: str,
    max_length: int = 176,
) -> str:
    prefix = f"{order}-{direction_prefix(direction)}-"
    subject_length = max(20, max_length - len(prefix))
    safe_subject = sanitize_windows_filename(subject, max_length=subject_length)
    return sanitize_windows_filename(f"{prefix}{safe_subject}", max_length=max_length)


def build_msg_filename(
    order: int,
    direction: Direction,
    subject: str,
    max_length: int = 180,
) -> str:
    stem = build_archive_stem(order, direction, subject, max_length=max_length - 4)
    return f"{stem}.msg"


def build_attachment_filename(
    mail_stem: str,
    original_filename: str,
    max_length: int = 180,
) -> str:
    safe_original = sanitize_windows_filename(original_filename, max_length=80)
    stem, extension = _split_extension(safe_original)
    base = f"{mail_stem} - {stem}"
    available_base_length = max_length - len(extension)
    return f"{sanitize_windows_filename(base, max_length=available_base_length)}{extension}"


def next_archive_order(folder: Path) -> int:
    if not folder.exists():
        return 1
    highest = 0
    for path in folder.iterdir():
        match = ARCHIVE_PREFIX_RE.match(path.name)
        if match is None:
            continue
        highest = max(highest, int(match.group("order")))
    return highest + 1


def suffix_copy_name(filename: str, copy_index: int) -> str:
    if copy_index < 2:
        msg = "copy_index starts at 2"
        raise ValueError(msg)
    if "." not in filename:
        return f"{filename} ({copy_index})"
    stem, extension = filename.rsplit(".", 1)
    return f"{stem} ({copy_index}).{extension}"


def _split_extension(filename: str) -> tuple[str, str]:
    if "." not in filename.strip("."):
        return filename, ""
    stem, extension = filename.rsplit(".", 1)
    return stem, f".{extension}"
