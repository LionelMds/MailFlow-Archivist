from __future__ import annotations

from pathlib import Path

import pytest

from mailflow.core.project_paths import (
    extract_project_number_from_folder_name,
    is_project_folder_name,
    local_project_path,
    normalize_project_filter,
    outlook_project_path,
    parse_project_number,
)


def test_parse_main_project_number() -> None:
    project = parse_project_number("2025-4893")

    assert project.year == "2025"
    assert project.sequence == "4893"
    assert project.main_number == "2025-4893"
    assert project.subproject is None


def test_parse_subproject_number() -> None:
    project = parse_project_number("2025-4893-2")

    assert project.main_number == "2025-4893"
    assert project.subproject == "2"
    assert project.is_subproject


@pytest.mark.parametrize("value", ["2025-4893", "2026-4995", "2026-123456"])
def test_project_folder_names(value: str) -> None:
    assert is_project_folder_name(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2025-4893 (Marquise porte F15)", "2025-4893"),
        ("2025-4893 Marquise porte F15", "2025-4893"),
    ],
)
def test_extract_project_number_from_folder_with_description(
    value: str,
    expected: str,
) -> None:
    assert is_project_folder_name(value)
    assert extract_project_number_from_folder_name(value) == expected


@pytest.mark.parametrize("value", ["25-4893", "2025-ABCD", "2025-4893-2", "foo"])
def test_reject_invalid_project_folder_names(value: str) -> None:
    assert not is_project_folder_name(value)


def test_local_project_path_uses_year_and_main_project() -> None:
    root = Path(r"C:\Clients")

    assert local_project_path(root, "2025-4893-2") == root / "2025" / "2025-4893"


def test_outlook_project_path() -> None:
    assert outlook_project_path("Boite de reception", "2025-4893") == (
        "Boite de reception/2025/2025-4893"
    )


def test_invalid_project_number_raises() -> None:
    with pytest.raises(ValueError):
        parse_project_number("2025/4893")


@pytest.mark.parametrize(
    ("year", "value", "expected"),
    [
        ("2025", "4893", "2025-4893"),
        ("2025", "2025-4893", "2025-4893"),
        ("2025", "2025-4893-2", "2025-4893"),
        ("2025", "", None),
        ("2025", None, None),
    ],
)
def test_normalize_project_filter(year: str, value: str | None, expected: str | None) -> None:
    assert normalize_project_filter(year, value) == expected
