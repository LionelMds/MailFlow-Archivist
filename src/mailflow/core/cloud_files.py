from __future__ import annotations

import os
import subprocess
from pathlib import PurePath


def request_local_availability(path: PurePath) -> None:
    """Ask Windows cloud-file providers to hydrate and keep a OneDrive file locally."""
    if not _is_windows() or not is_probably_onedrive_path(path):
        return
    try:
        _run_attrib(["attrib", "+P", "-U", str(path)])
    except OSError:
        return


def is_probably_onedrive_path(path: PurePath) -> bool:
    return any(part.casefold().startswith("onedrive") for part in path.parts)


def _is_windows() -> bool:
    return os.name == "nt"


def _run_attrib(args: list[str]) -> None:
    subprocess.run(
        args,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
