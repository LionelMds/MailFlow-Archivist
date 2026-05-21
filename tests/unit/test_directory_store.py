from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mailflow.core.contact_directory import ContactObservation
from mailflow.storage.directory_store import SQLiteDirectoryStore


def observation(
    email: str,
    *,
    display_name: str = "AIG",
    organization_name: str = "AIG",
    domain: str = "gva.ch",
) -> ContactObservation:
    return ContactObservation(
        project_number="2025-4893",
        email=email,
        display_name=display_name,
        organization_name=organization_name,
        domain=domain,
        source="test",
        confidence=0.9,
        allow_domain_mapping=True,
    )


def test_directory_store_records_domain_contact_and_project(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")

    first = store.record_observation(observation("contact@gva.ch"))
    second = store.record_observation(observation("chef@gva.ch", display_name="Chef AIG"))

    assert first.new_organization
    assert first.new_domain
    assert first.new_contact
    assert first.new_project_participant
    assert not second.new_organization
    assert not second.new_domain
    assert second.new_contact
    assert not second.new_project_participant
    assert store.organization_name_for_email("nouveau@gva.ch") == "AIG"
    assert store.count_organizations() == 1
    assert store.count_domains() == 1
    assert store.count_contacts() == 2


def test_directory_store_keeps_generic_domain_as_contact_only(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    item = observation(
        "atelier@gmail.com",
        display_name="Atelier Example SA",
        organization_name="Atelier Example SA",
        domain="gmail.com",
    )

    result = store.record_observation(replace(item, allow_domain_mapping=False))

    assert result.new_organization
    assert not result.new_domain
    assert store.organization_name_for_email("atelier@gmail.com") == "Atelier Example SA"
    assert store.organization_name_for_email("autre@gmail.com") is None
