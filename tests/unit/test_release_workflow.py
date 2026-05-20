from __future__ import annotations

from pathlib import Path

RELEASE_WORKFLOW = Path(".github/workflows/release.yml")
WINDOWS_INSTALLER_SCRIPT = Path("packaging/windows/MailFlow-Archivist.iss")


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


def test_release_publishes_installers_not_zip_archives() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "MailFlow-Archivist-Setup.exe" in workflow
    assert "MailFlow-Archivist.dmg" in workflow
    assert "hdiutil create" in workflow
    assert "ln -s /Applications" in workflow
    assert "Inno Setup" in workflow
    assert "release-assets/*.exe" in workflow
    assert "release-assets/*.dmg" in workflow
    assert "release-assets/*.zip" not in workflow


def test_windows_installer_is_per_user_and_update_aware() -> None:
    installer_script = WINDOWS_INSTALLER_SCRIPT.read_text(encoding="utf-8")

    assert "DefaultDirName={localappdata}\\Programs\\MailFlow Archivist" in installer_script
    assert "PrivilegesRequired=lowest" in installer_script
    assert "AppUpdatesURL=https://github.com/LionelMds/MailFlow-Archivist/releases/latest" in (
        installer_script
    )
    assert "MailFlow-Archivist.exe" in installer_script
