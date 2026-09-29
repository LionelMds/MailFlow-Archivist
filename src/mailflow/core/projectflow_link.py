"""Ask ProjectFlow Automator to prepare the Outlook folder of known projects.

ProjectFlow creates Balz Metal projects and names their folders. MailFlow never creates a
project folder itself: for the numbers it found without an Outlook folder, it asks
ProjectFlow, which only acts on projects present in its repertoire chantier and names the
folder with its own Outlook arborescence. The exchange goes through two JSON files
because the packaged ProjectFlow has no console.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION = 1
ENSURE_OUTLOOK_FOLDERS = "ensure_outlook_folders"
REQUEST_ARGUMENT = "--mailflow-request"
RESULT_ARGUMENT = "--mailflow-result"
MINIMUM_PROJECTFLOW_VERSION = (0, 1, 57)
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_NUMBERS_PER_REQUEST = 50
EXECUTABLE_NAME = "ProjectFlowAutomator.exe"
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    r"\{C91D6B6C-0AC5-47F9-80EF-1665EC55D1F6}_is1"
)

# Runs the command and returns once it ends; raises TimeoutError past the delay.
ProcessRunner = Callable[[list[str], float], None]


class ProjectFlowError(RuntimeError):
    """A locally worded failure, safe to display."""


class ProjectFlowProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: str
    status: Literal["ok", "unknown", "error"]
    designation: str = ""
    societe: str = ""
    folder_paths: list[list[str]] = Field(default_factory=list)
    message: str = ""


class ProjectFlowOutlookTarget(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mailbox: str = ""
    base_folder: str = ""


class ProjectFlowResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    protocol: int
    ok: bool
    error: str | None = None
    projectflow_version: str = ""
    outlook: ProjectFlowOutlookTarget = Field(default_factory=ProjectFlowOutlookTarget)
    projects: list[ProjectFlowProject] = Field(default_factory=list)


@dataclass(frozen=True)
class ProjectFlowInstallation:
    executable: Path | None
    version: str | None = None

    @property
    def supported(self) -> bool:
        """False only for an installed version known to predate MailFlow requests."""
        parsed = parse_version(self.version)
        return self.executable is not None and (
            parsed is None or parsed >= MINIMUM_PROJECTFLOW_VERSION
        )

    @property
    def status_text(self) -> str:
        if self.executable is None:
            return "ProjectFlow Automator introuvable : indiquez son emplacement."
        version = f" {self.version}" if self.version else ""
        if not self.supported:
            minimum = ".".join(str(part) for part in MINIMUM_PROJECTFLOW_VERSION)
            return (
                f"ProjectFlow{version} détecté : mettez-le à jour (version {minimum} "
                "ou plus récente)."
            )
        return f"ProjectFlow{version} détecté : {self.executable}"


@dataclass(frozen=True)
class ProjectFlowReport:
    ready: tuple[str, ...] = ()
    elsewhere: tuple[str, ...] = ()
    unknown: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


def default_projectflow_locations() -> list[Path]:
    locations = []
    for variable, relative in (
        ("LOCALAPPDATA", Path("Programs") / "ProjectFlow Automator"),
        ("ProgramFiles", Path("ProjectFlow Automator")),
    ):
        base = os.environ.get(variable, "").strip()
        if base:
            locations.append(Path(base) / relative / EXECUTABLE_NAME)
    return locations


def find_projectflow(
    configured: str | None,
    *,
    installed: Callable[[], tuple[Path | None, str | None]] | None = None,
) -> ProjectFlowInstallation:
    """Use the configured program, else the installed one; read its installed version."""
    install_dir, version = (installed or installed_projectflow)()
    cleaned = (configured or "").strip().strip('"')
    if cleaned:
        executable = Path(cleaned).expanduser()
        if not executable.is_file():
            return ProjectFlowInstallation(None)
        same_install = install_dir is not None and _same_path(executable.parent, install_dir)
        return ProjectFlowInstallation(executable, version if same_install else None)
    candidates = [install_dir / EXECUTABLE_NAME] if install_dir is not None else []
    for candidate in [*candidates, *default_projectflow_locations()]:
        if candidate.is_file():
            same_install = install_dir is not None and _same_path(candidate.parent, install_dir)
            return ProjectFlowInstallation(candidate, version if same_install else None)
    return ProjectFlowInstallation(None)


def installed_projectflow() -> tuple[Path | None, str | None]:
    """Install folder and version recorded by the ProjectFlow installer (Windows)."""
    if not sys.platform.startswith("win"):
        return None, None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location = _registry_text(winreg, key, "InstallLocation")
            version = _registry_text(winreg, key, "DisplayVersion")
            if not version:
                # Older installers only put the version at the end of the display name.
                last_word = _registry_text(winreg, key, "DisplayName").rsplit(" ", 1)[-1]
                version = last_word if parse_version(last_word) else ""
    except OSError:
        return None, None
    return (Path(location) if location else None), (version or None)


def parse_version(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    parts = value.strip().lstrip("v").split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


class ProjectFlowLink:
    def __init__(
        self,
        executable: Path,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        runner: ProcessRunner | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._runner = runner or run_hidden_process

    def ensure_outlook_folders(self, numbers: Sequence[str]) -> ProjectFlowResult:
        wanted = list(dict.fromkeys(number.strip() for number in numbers if number.strip()))
        if not wanted:
            raise ValueError("Aucun numéro de projet à transmettre à ProjectFlow.")
        results = [
            self._request(wanted[start:start + MAX_NUMBERS_PER_REQUEST])
            for start in range(0, len(wanted), MAX_NUMBERS_PER_REQUEST)
        ]
        merged = results[-1].model_copy(
            update={"projects": [project for result in results for project in result.projects]}
        )
        return merged

    def _request(self, numbers: list[str]) -> ProjectFlowResult:
        with tempfile.TemporaryDirectory(prefix="mailflow-projectflow-") as temp_dir:
            request_path = Path(temp_dir) / "demande.json"
            result_path = Path(temp_dir) / "resultat.json"
            request_path.write_text(
                json.dumps({
                    "protocol": PROTOCOL_VERSION,
                    "action": ENSURE_OUTLOOK_FOLDERS,
                    "numbers": numbers,
                }),
                encoding="utf-8",
            )
            command = [
                str(self.executable),
                REQUEST_ARGUMENT, str(request_path),
                RESULT_ARGUMENT, str(result_path),
            ]
            try:
                self._runner(command, self.timeout_seconds)
            except TimeoutError as exc:
                raise ProjectFlowError(
                    f"ProjectFlow n'a pas répondu en {self.timeout_seconds:.0f} secondes. "
                    "Vérifiez qu'Outlook est ouvert, puis relancez."
                ) from exc
            except OSError as exc:
                raise ProjectFlowError(
                    f"ProjectFlow Automator ne peut pas être lancé : {self.executable}"
                ) from exc
            if not result_path.is_file():
                minimum = ".".join(str(part) for part in MINIMUM_PROJECTFLOW_VERSION)
                raise ProjectFlowError(
                    "ProjectFlow n'a renvoyé aucune réponse : installez ProjectFlow "
                    f"{minimum} ou plus récent."
                )
            try:
                result = ProjectFlowResult.model_validate_json(
                    result_path.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError) as exc:
                raise ProjectFlowError("Réponse de ProjectFlow illisible.") from exc
        if result.protocol != PROTOCOL_VERSION:
            raise ProjectFlowError(
                "ProjectFlow répond dans un format inconnu : mettez à jour MailFlow."
            )
        if not result.ok:
            raise ProjectFlowError(result.error or "ProjectFlow a refusé la demande.")
        return result


def run_hidden_process(command: list[str], timeout_seconds: float) -> None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        # Never kill it: an outdated ProjectFlow may have opened its window instead.
        raise TimeoutError from exc


def projectflow_report(
    result: ProjectFlowResult,
    visible_numbers: Iterable[str],
) -> ProjectFlowReport:
    """Compare ProjectFlow's answer with the project folders MailFlow now sees."""
    visible = set(visible_numbers)
    ready: list[str] = []
    elsewhere: list[str] = []
    unknown: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for project in result.projects:
        if project.status == "ok":
            (ready if project.number in visible else elsewhere).append(project.number)
        elif project.status == "unknown":
            unknown.append((project.number, project.message))
        else:
            failed.append((project.number, project.message))
    return ProjectFlowReport(
        ready=tuple(ready),
        elsewhere=tuple(elsewhere),
        unknown=tuple(unknown),
        failed=tuple(failed),
    )


def _registry_text(winreg_module: Any, key: Any, name: str) -> str:
    try:
        value, _kind = winreg_module.QueryValueEx(key, name)
    except OSError:
        return ""
    return str(value or "").strip()


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
