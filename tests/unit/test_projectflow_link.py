from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from mailflow.core.projectflow_link import (
    ENSURE_OUTLOOK_FOLDERS,
    EXECUTABLE_NAME,
    MAX_NUMBERS_PER_REQUEST,
    PROTOCOL_VERSION,
    REQUEST_ARGUMENT,
    RESULT_ARGUMENT,
    ProjectFlowError,
    ProjectFlowLink,
    ProjectFlowResult,
    find_projectflow,
    parse_version,
    projectflow_report,
    run_hidden_process,
)


def executable(tmp_path: Path, folder: str = "ProjectFlow Automator") -> Path:
    path = tmp_path / folder / EXECUTABLE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


def test_installed_projectflow_is_found_with_its_version(tmp_path: Path) -> None:
    program = executable(tmp_path)

    found = find_projectflow("", installed=lambda: (program.parent, "0.1.57"))

    assert found.executable == program
    assert found.version == "0.1.57"
    assert found.supported
    assert found.status_text == f"ProjectFlow 0.1.57 détecté : {program}"


def test_outdated_projectflow_asks_for_an_update(tmp_path: Path) -> None:
    program = executable(tmp_path)

    found = find_projectflow(None, installed=lambda: (program.parent, "0.1.56"))

    assert not found.supported
    assert "mettez-le à jour (version 0.1.57" in found.status_text


def test_configured_program_wins_and_keeps_the_version_only_if_installed(
    tmp_path: Path,
) -> None:
    installed = executable(tmp_path)
    custom = executable(tmp_path, "Dev")

    same = find_projectflow(f'"{installed}"', installed=lambda: (installed.parent, "0.1.56"))
    other = find_projectflow(str(custom), installed=lambda: (installed.parent, "0.1.56"))
    missing = find_projectflow(str(tmp_path / "absent.exe"), installed=lambda: (None, None))

    assert same.executable == installed and not same.supported
    assert other.executable == custom and other.version is None and other.supported
    assert missing.executable is None
    assert not missing.supported
    assert "introuvable" in missing.status_text


def test_default_install_folder_is_used_without_installer_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = executable(tmp_path / "Programs")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nothing"))

    found = find_projectflow("", installed=lambda: (None, None))

    assert found.executable == program
    assert found.supported


def test_versions_are_compared_numerically() -> None:
    assert parse_version("0.1.57") == (0, 1, 57)
    assert parse_version("v1.2") == (1, 2)
    assert parse_version("0.1.10") > (0, 1, 9)  # type: ignore[operator]
    assert parse_version("ProjectFlow") is None
    assert parse_version("") is None


class FakeProjectFlow:
    """Plays ProjectFlow: reads the request file and writes the answer."""

    def __init__(self, answer: Any = None, *, write: bool = True) -> None:
        self.answer = answer
        self.write = write
        self.requests: list[dict[str, Any]] = []
        self.commands: list[list[str]] = []
        self.timeouts: list[float] = []

    def __call__(self, command: list[str], timeout: float) -> None:
        self.commands.append(command)
        self.timeouts.append(timeout)
        request_path = Path(command[command.index(REQUEST_ARGUMENT) + 1])
        result_path = Path(command[command.index(RESULT_ARGUMENT) + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.requests.append(request)
        if not self.write:
            return
        answer = self.answer(request) if callable(self.answer) else self.answer
        result_path.write_text(
            answer if isinstance(answer, str) else json.dumps(answer), encoding="utf-8",
        )


def ok_answer(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": True,
        "error": None,
        "projectflow_version": "0.1.57",
        "outlook": {"mailbox": "lionel@balzmetal.ch", "base_folder": "inbox"},
        "projects": [
            {"number": number, "status": "ok", "folder_paths": [["2026", f"{number} (X)"]]}
            for number in request["numbers"]
        ],
    }


def test_request_lists_the_numbers_and_reads_the_answer(tmp_path: Path) -> None:
    program = executable(tmp_path)
    fake = FakeProjectFlow(ok_answer)

    result = ProjectFlowLink(program, timeout_seconds=30, runner=fake).ensure_outlook_folders(
        ["2026-0150", " 2026-0151 ", "2026-0150", ""]
    )

    assert fake.requests == [{
        "protocol": PROTOCOL_VERSION,
        "action": ENSURE_OUTLOOK_FOLDERS,
        "numbers": ["2026-0150", "2026-0151"],
    }]
    assert fake.commands[0][0] == str(program)
    assert fake.timeouts == [30]
    assert [project.number for project in result.projects] == ["2026-0150", "2026-0151"]
    assert result.outlook.base_folder == "inbox"
    # The exchange files are removed once read.
    assert not Path(fake.commands[0][2]).exists()


def test_large_requests_are_split(tmp_path: Path) -> None:
    fake = FakeProjectFlow(ok_answer)
    numbers = [f"2026-{index:04d}" for index in range(MAX_NUMBERS_PER_REQUEST + 3)]

    result = ProjectFlowLink(executable(tmp_path), runner=fake).ensure_outlook_folders(numbers)

    assert [len(request["numbers"]) for request in fake.requests] == [
        MAX_NUMBERS_PER_REQUEST, 3,
    ]
    assert len(result.projects) == len(numbers)


def test_empty_request_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Aucun numéro"):
        ProjectFlowLink(executable(tmp_path), runner=FakeProjectFlow()).ensure_outlook_folders(
            [" "]
        )


def raise_error(error: Exception) -> Any:
    def runner(_command: list[str], _timeout: float) -> None:
        raise error

    return runner


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (FakeProjectFlow(write=False), "installez ProjectFlow 0.1.57"),
        (raise_error(TimeoutError()), "n'a pas répondu en 120 secondes"),
        (raise_error(FileNotFoundError()), "ne peut pas être lancé"),
        (FakeProjectFlow("{not json"), "Réponse de ProjectFlow illisible."),
        (FakeProjectFlow({"protocol": 2, "ok": True}), "format inconnu"),
        (
            FakeProjectFlow({"protocol": 1, "ok": False, "error": "Outlook desactive."}),
            "Outlook desactive.",
        ),
        (FakeProjectFlow({"protocol": 1, "ok": False}), "a refusé la demande"),
    ],
)
def test_failures_become_local_messages(tmp_path: Path, runner: Any, message: str) -> None:
    with pytest.raises(ProjectFlowError, match=message):
        ProjectFlowLink(executable(tmp_path), runner=runner).ensure_outlook_folders(
            ["2026-0150"]
        )


def test_report_separates_ready_elsewhere_unknown_and_failed() -> None:
    result = ProjectFlowResult.model_validate({
        "protocol": 1,
        "ok": True,
        "projects": [
            {"number": "2026-0150", "status": "ok"},
            {"number": "2026-0152", "status": "ok"},
            {"number": "2026-0999", "status": "unknown", "message": "Absent."},
            {"number": "2026-0151", "status": "error", "message": "Refus Outlook."},
        ],
    })

    report = projectflow_report(result, {"2026-0150": object()})

    assert report.ready == ("2026-0150",)
    assert report.elsewhere == ("2026-0152",)
    assert report.unknown == (("2026-0999", "Absent."),)
    assert report.failed == (("2026-0151", "Refus Outlook."),)


def test_hidden_process_waits_and_times_out_without_killing() -> None:
    run_hidden_process([sys.executable, "-c", "pass"], 30)

    with pytest.raises(TimeoutError):
        run_hidden_process([sys.executable, "-c", "import time; time.sleep(1.5)"], 0.05)
