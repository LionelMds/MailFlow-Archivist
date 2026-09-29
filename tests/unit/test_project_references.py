from __future__ import annotations

import pytest

from mailflow.core.project_references import (
    ProjectReference,
    ReferenceSource,
    find_project_numbers,
    is_year_range,
    merge_references,
)

EN_DASH = chr(0x2013)
NON_BREAKING_HYPHEN = chr(0x2011)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Balcons 2025-4893 Villa Dupont", ["2025-4893"]),
        ("RE: offre 2025-4893-2 (lot serrurerie)", ["2025-4893"]),
        (
            f"Projet 2025 {EN_DASH} 5012 et 2025{NON_BREAKING_HYPHEN}4893",
            ["2025-5012", "2025-4893"],
        ),
        ("2025-4893, rappel 2025-4893, puis 2026-0012", ["2025-4893", "2026-0012"]),
        ("Ancien dossier 2019-873", ["2019-873"]),
    ],
)
def test_project_numbers_are_found_in_order_without_duplicates(
    text: str, expected: list[str],
) -> None:
    assert find_project_numbers(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Réunion du 2025-09-29",
        "Tel +41 21 2025-123456",
        "Commande 12025-4893",
        "Référence 2025-48931234",
        "Norme 1990-1234",
        "IBAN CH93 0076 2011 6238 5295 7",
    ],
)
def test_other_numbers_are_not_project_numbers(text: str) -> None:
    assert find_project_numbers(text) == []


def test_references_keep_every_source_of_a_number() -> None:
    references = merge_references([
        (ReferenceSource.SUBJECT, ["2025-4893"]),
        (ReferenceSource.BODY, ["2025-5012", "2025-4893"]),
        (ReferenceSource.ATTACHMENT_NAME, []),
    ])

    assert references == [
        ProjectReference("2025-4893", (ReferenceSource.SUBJECT, ReferenceSource.BODY)),
        ProjectReference("2025-5012", (ReferenceSource.BODY,)),
    ]


def test_year_ranges_are_recognized() -> None:
    assert is_year_range("2024-2025")
    assert not is_year_range("2025-2025")
    assert not is_year_range("2025-4893")
