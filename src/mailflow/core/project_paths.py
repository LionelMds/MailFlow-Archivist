from __future__ import annotations

import re
from pathlib import Path

from mailflow.models import ProjectRef

PROJECT_FOLDER_RE = re.compile(r"^(?P<year>20\d{2})-(?P<sequence>\d{3,})$")
PROJECT_FOLDER_PREFIX_RE = re.compile(
    r"^(?P<number>(?P<year>20\d{2})-(?P<sequence>\d{3,}))(?=$|[\s(])"
)
PROJECT_NUMBER_RE = re.compile(r"^(?P<year>20\d{2})-(?P<sequence>\d{3,})(?:-(?P<sub>\d+))?$")
PROJECT_SEQUENCE_RE = re.compile(r"^\d{3,}$")


def parse_project_number(value: str) -> ProjectRef:
    normalized = value.strip()
    match = PROJECT_NUMBER_RE.fullmatch(normalized)
    if not match:
        msg = f"Invalid project number: {value!r}"
        raise ValueError(msg)
    return ProjectRef(
        number=normalized,
        year=match.group("year"),
        sequence=match.group("sequence"),
        subproject=match.group("sub"),
    )


def is_project_folder_name(name: str) -> bool:
    return extract_project_number_from_folder_name(name) is not None


def extract_project_number_from_folder_name(name: str) -> str | None:
    normalized = name.strip()
    strict_match = PROJECT_FOLDER_RE.fullmatch(normalized)
    if strict_match:
        return normalized
    prefix_match = PROJECT_FOLDER_PREFIX_RE.match(normalized)
    if prefix_match:
        return prefix_match.group("number")
    return None


def normalize_project_filter(year: str, value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if PROJECT_SEQUENCE_RE.fullmatch(cleaned):
        return f"{year.strip()}-{cleaned}"
    return parse_project_number(cleaned).main_number


def local_project_path(projects_root: Path, project_number: str) -> Path:
    project = parse_project_number(project_number)
    return projects_root / project.year / project.main_number


def outlook_project_path(root_folder: str, project_number: str) -> str:
    project = parse_project_number(project_number)
    return "/".join([root_folder.strip("/"), project.year, project.main_number])
