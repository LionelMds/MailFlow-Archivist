from __future__ import annotations

from datetime import datetime

from mailflow.core.contact_directory import (
    ContactObservation,
    DirectoryUpsertOutcome,
    contact_observation_from_reference,
    import_contact_directory_from_mails,
)
from mailflow.models import Direction, MailMetadata


class FakeDirectoryStore:
    def __init__(self) -> None:
        self.observations: list[ContactObservation] = []

    def record_observation(self, observation: ContactObservation) -> DirectoryUpsertOutcome:
        self.observations.append(observation)
        return DirectoryUpsertOutcome(
            new_organization=True,
            new_domain=observation.allow_domain_mapping,
            new_contact=True,
            new_project_participant=True,
        )


def test_contact_observation_prefers_parenthesized_company() -> None:
    observation = contact_observation_from_reference(
        project_number="2025-4893",
        contact="Lorenzo D'Angelo (HANS KOHLER AG) <l.dangelo@kohler.ch>",
    )

    assert observation is not None
    assert observation.organization_name == "HANS KOHLER AG"
    assert observation.domain == "kohler.ch"
    assert observation.allow_domain_mapping


def test_contact_directory_import_skips_internal_and_generic_personal_domains() -> None:
    mail = MailMetadata(
        entry_id="ENTRY-1",
        project_number="2025-4893",
        outlook_folder="Boite de reception/2025/2025-4893",
        direction=Direction.SENT,
        subject="Projet",
        sender_name="Lionel",
        sender_email="lionel@balzmetal.ch",
        recipients=[
            "AIG <contact@gva.ch>",
            "Jean Dupont <jean.dupont@gmail.com>",
        ],
        sent_at=datetime(2026, 5, 6, 10, 30),
    )
    store = FakeDirectoryStore()

    result = import_contact_directory_from_mails([mail], store)

    assert result.scanned_mail_count == 1
    assert result.observed_contact_count == 2
    assert result.imported_contact_count == 1
    assert result.skipped_internal_count == 1
    assert result.skipped_generic_domain_count == 1
    assert store.observations[0].organization_name == "GVA"
