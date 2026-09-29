"""Find Balz Metal project numbers (20XX-XXXX) quoted in a mail."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# 2025-4893, also written with a dash variant or a space around it. A sub-project
# suffix (2025-4893-2) keeps its main number. Digits glued before or after reject
# longer numbers such as IBAN, phone or supplier order numbers.
PROJECT_REFERENCE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2}) ?[-\u2010\u2011\u2012\u2013] ?(?P<sequence>\d{3,5})(?!\d)"
)


class ReferenceSource(StrEnum):
    SUBJECT = "objet"
    BODY = "corps"
    ATTACHMENT_NAME = "nom de pièce jointe"
    ATTACHMENT_CONTENT = "contenu de pièce jointe"


@dataclass(frozen=True)
class ProjectReference:
    number: str
    sources: tuple[ReferenceSource, ...]


def find_project_numbers(text: str) -> list[str]:
    """Return the main project numbers of a text, in order of first appearance."""
    numbers: dict[str, None] = {}
    for match in PROJECT_REFERENCE_RE.finditer(text):
        numbers.setdefault(f"{match['year']}-{match['sequence']}", None)
    return list(numbers)


def merge_references(
    found: Iterable[tuple[ReferenceSource, Iterable[str]]],
) -> list[ProjectReference]:
    """Group numbers by project, keeping the order of the sources that were read."""
    sources_by_number: dict[str, list[ReferenceSource]] = {}
    for source, numbers in found:
        for number in numbers:
            sources = sources_by_number.setdefault(number, [])
            if source not in sources:
                sources.append(source)
    return [
        ProjectReference(number=number, sources=tuple(sources))
        for number, sources in sources_by_number.items()
    ]


def is_year_range(number: str) -> bool:
    """True for "2024-2025", a season or period rather than a project number."""
    year, _separator, sequence = number.partition("-")
    return sequence.isdigit() and year.isdigit() and int(sequence) == int(year) + 1
