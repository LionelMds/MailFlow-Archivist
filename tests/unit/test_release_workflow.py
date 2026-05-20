from __future__ import annotations

from pathlib import Path

RELEASE_WORKFLOW = Path(".github/workflows/release.yml")


def test_release_build_does_not_collect_all_pyside6() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "--collect-all PySide6" not in workflow
    assert "--exclude-module PySide6.QtWebEngineCore" in workflow
    assert "--exclude-module PySide6.QtWebEngineWidgets" in workflow


def test_release_build_installs_runtime_dependencies_only() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    build_section = workflow.split("  build:", maxsplit=1)[1]

    assert 'python -m pip install "pyinstaller>=6.10"' in build_section
    assert 'python -m pip install .' in build_section
    assert 'python -m pip install -e ".[dev]"' not in build_section
