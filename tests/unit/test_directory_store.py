from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mailflow.core.contact_directory import ContactObservation
from mailflow.models import InterlocutorType
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


def test_directory_store_lists_organizations_for_ui(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.record_observation(observation("contact@gva.ch", display_name="Jean AIG"))
    store.record_observation(
        replace(
            observation("achat@gva.ch", display_name="Marie AIG"),
            project_number="2026-4995",
        )
    )

    entries = store.list_organizations()

    assert len(entries) == 1
    assert entries[0].name == "AIG"
    assert entries[0].domains == ("gva.ch",)
    assert entries[0].contacts == (
        "Marie AIG <achat@gva.ch>",
        "Jean AIG <contact@gva.ch>",
    )
    assert entries[0].project_count == 2
    assert entries[0].default_role == InterlocutorType.INCONNU


def test_directory_store_renames_organization(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.record_observation(observation("contact@gva.ch"))
    organization_id = store.list_organizations()[0].organization_id

    store.rename_organization(organization_id, "Aeroport International Geneve")

    assert store.organization_name_for_email("nouveau@gva.ch") == (
        "Aeroport International Geneve"
    )
    assert store.list_organizations()[0].name == "Aeroport International Geneve"


def test_directory_store_merges_organizations(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.record_observation(observation("contact@gva.ch"))
    store.record_observation(
        observation(
            "vente@metalfactory.ch",
            display_name="Metal Factory",
            organization_name="Metal Factory",
            domain="metalfactory.ch",
        )
    )
    entries_by_name = {entry.name: entry.organization_id for entry in store.list_organizations()}

    store.merge_organizations(entries_by_name["Metal Factory"], entries_by_name["AIG"])

    entries = store.list_organizations()
    assert len(entries) == 1
    assert entries[0].name == "AIG"
    assert entries[0].domains == ("gva.ch", "metalfactory.ch")
    assert store.organization_name_for_email("vente@metalfactory.ch") == "AIG"
    assert store.count_organizations() == 1
    assert store.count_contacts() == 2


def test_directory_store_adds_manual_organization_with_global_role(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")

    organization_id = store.add_organization(
        "Metal Factory",
        domain="@MetalFactory.ch",
        role=InterlocutorType.FOURNISSEUR,
    )

    assert organization_id > 0
    assert store.organization_name_for_email("vente@metalfactory.ch") == "Metal Factory"
    assert store.interlocutor_for_email("2025-4893", "vente@metalfactory.ch") == (
        InterlocutorType.FOURNISSEUR
    )
    assert store.interlocutor_for_email("2026-4995", "vente@metalfactory.ch") == (
        InterlocutorType.FOURNISSEUR
    )
    assert store.list_organizations()[0].default_role == InterlocutorType.FOURNISSEUR


def test_directory_store_keeps_global_roles_independent_for_each_company(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.add_organization(
        "Metal Factory",
        domain="metalfactory.ch",
        role=InterlocutorType.FOURNISSEUR,
    )
    store.add_organization(
        "Hans Kohler",
        domain="kohler.ch",
        role=InterlocutorType.INTERVENANT_EXTERNE,
    )

    roles = {
        entry.name: entry.default_role
        for entry in store.list_organizations()
    }
    assert roles == {
        "Hans Kohler": InterlocutorType.INTERVENANT_EXTERNE,
        "Metal Factory": InterlocutorType.FOURNISSEUR,
    }
    assert store.interlocutor_for_email("2024-4788", "vente@metalfactory.ch") == (
        InterlocutorType.FOURNISSEUR
    )


def test_directory_store_global_role_replaces_existing_project_roles(tmp_path: Path) -> None:
    db_path = tmp_path / "mailflow.sqlite"
    store = SQLiteDirectoryStore(db_path)
    store.record_observation(observation("contact@gva.ch"))
    store.record_observation(
        replace(observation("autre@gva.ch"), project_number="2026-4995")
    )
    organization_id = store.list_organizations()[0].organization_id
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE project_participants SET role = 'client' WHERE organization_id = ?",
            (organization_id,),
        )

    store.set_organization_role(organization_id, InterlocutorType.FOURNISSEUR)

    assert store.interlocutor_for_email("2025-4893", "contact@gva.ch") == (
        InterlocutorType.FOURNISSEUR
    )
    assert store.interlocutor_for_email("2026-4995", "contact@gva.ch") == (
        InterlocutorType.FOURNISSEUR
    )
    assert store.interlocutor_for_email("2030-9999", "contact@gva.ch") == (
        InterlocutorType.FOURNISSEUR
    )


def test_directory_store_legacy_project_role_cannot_override_global_role(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mailflow.sqlite"
    store = SQLiteDirectoryStore(db_path)
    organization_id = store.add_organization(
        "AIG",
        domain="gva.ch",
        role=InterlocutorType.CLIENT,
    )
    store.record_observation(observation("contact@gva.ch"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE project_participants SET role = 'fournisseur' WHERE organization_id = ?",
            (organization_id,),
        )

    assert store.interlocutor_for_email("2025-4893", "contact@gva.ch") == (
        InterlocutorType.CLIENT
    )
    assert store.interlocutor_for_email("2026-4995", "contact@gva.ch") == (
        InterlocutorType.CLIENT
    )


def test_directory_store_migrates_consistent_legacy_project_role_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mailflow.sqlite"
    store = SQLiteDirectoryStore(db_path)
    store.record_observation(observation("contact@gva.ch"))
    organization_id = store.list_organizations()[0].organization_id
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE project_participants SET role = 'client' WHERE organization_id = ?",
            (organization_id,),
        )
        connection.execute(
            "DELETE FROM directory_meta WHERE key = 'global_organization_roles_v1'"
        )

    migrated = SQLiteDirectoryStore(db_path).list_organizations()

    assert migrated[0].default_role == InterlocutorType.CLIENT


def test_directory_store_rejects_duplicate_manual_domain(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.add_organization("AIG", domain="gva.ch")

    with pytest.raises(ValueError, match="appartient deja"):
        store.add_organization("Geneve Aeroport", domain="@GVA.CH")


def test_directory_store_deletes_organization_and_related_records(tmp_path: Path) -> None:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    store.record_observation(observation("contact@gva.ch"))
    organization_id = store.list_organizations()[0].organization_id

    store.delete_organization(organization_id)

    assert store.list_organizations() == []
    with sqlite3.connect(tmp_path / "mailflow.sqlite") as connection:
        participant_count = connection.execute(
            "SELECT COUNT(*) FROM project_participants"
        ).fetchone()
    assert participant_count == (0,)
    assert store.organization_name_for_email("contact@gva.ch") is None
    assert store.count_domains() == 0
    assert store.count_contacts() == 0
