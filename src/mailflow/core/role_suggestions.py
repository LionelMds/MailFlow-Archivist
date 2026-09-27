"""Suggest a directory role for companies the directory does not know yet.

The AI engine may estimate, for each mail of an unknown company, whether that company
is a supplier or a client of Balz Metal. This module averages those estimates per
company. The result is only shown to the user: a role enters the directory only when
the user validates it, and routing always reads the directory.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mailflow.classifier.routing_context import primary_external_email
from mailflow.models import REVIEW_CONFIDENCE_THRESHOLD, InterlocutorType, PreviewRow

SUGGESTABLE_ROLES = {
    "fournisseur": InterlocutorType.FOURNISSEUR,
    "client": InterlocutorType.CLIENT,
}
ROLE_LABELS = {
    "fournisseur": "Fournisseur",
    "client": "Client",
    "autre": "Ni client ni fournisseur",
}


@dataclass(frozen=True)
class RoleSuggestion:
    organization_id: int
    key: str
    probability: float
    mail_count: int

    @property
    def role(self) -> InterlocutorType | None:
        """The directory role to apply, or None when no business role is suggested."""
        return SUGGESTABLE_ROLES.get(self.key)

    @property
    def is_confident(self) -> bool:
        return self.role is not None and self.probability >= REVIEW_CONFIDENCE_THRESHOLD

    @property
    def label(self) -> str:
        mails = "1 mail" if self.mail_count == 1 else f"{self.mail_count} mails"
        return f"{ROLE_LABELS.get(self.key, self.key)} · {self.probability:.0%} · {mails}"


def suggest_roles(
    rows: Iterable[PreviewRow],
    organization_id_for_email: Callable[[str], int | None],
) -> dict[int, RoleSuggestion]:
    totals: dict[int, dict[str, float]] = {}
    counts: dict[int, int] = {}
    for row in rows:
        estimate = row.classification.role_estimate
        probabilities = estimate.probabilities if estimate is not None else {}
        if not probabilities:
            continue
        email = primary_external_email(row.mail)
        organization_id = organization_id_for_email(email) if email else None
        if organization_id is None:
            continue
        total = totals.setdefault(organization_id, {})
        for key, value in probabilities.items():
            total[key] = total.get(key, 0.0) + value
        counts[organization_id] = counts.get(organization_id, 0) + 1
    suggestions = {}
    for organization_id, total in totals.items():
        count = counts[organization_id]
        key = max(total, key=lambda name: total[name])
        suggestions[organization_id] = RoleSuggestion(
            organization_id=organization_id,
            key=key,
            probability=total[key] / count,
            mail_count=count,
        )
    return suggestions
