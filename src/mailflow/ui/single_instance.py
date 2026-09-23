"""Prevent two MailFlow windows from watching and archiving the same mailbox."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile

LOCK_FILE_NAME = "mailflow.lock"


def acquire_instance_lock(data_dir: Path) -> QLockFile | None:
    """Return the held lock, or None when another MailFlow instance already runs.

    The caller must keep the returned object alive for the whole session.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / LOCK_FILE_NAME))
    # The app holds the lock for hours: never treat it as stale because of its age.
    # A lock left by a crashed process is still recovered via its PID.
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return None
    return lock
