from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.models import InterlocutorType, MailType, ManualLearningSignal, RoutingCategory
from mailflow.storage.learning_store import SQLiteLearningStore


def signal(*, mail_id: str = "ENTRY-1") -> ManualLearningSignal:
    return ManualLearningSignal(
        mail_id=mail_id,
        project_number="2025-4893",
        subject="Validation de la phase commerciale",
        selected_mail_type=MailType.DEMANDE_DE_PRIX,
        selected_interlocutor=InterlocutorType.FOURNISSEUR,
        selected_target_folder="Fournisseurs/Demande de prix/Metal Factory",
        learning_term=None,
        misleading_term=None,
        manual_required=False,
        created_at=datetime(2026, 5, 6, 10, 30),
        organization_name="Metal Factory",
        primary_email="sales@metal.test",
    )


def test_learning_store_records_verified_routing_example(tmp_path: Path) -> None:
    store = SQLiteLearningStore(tmp_path / "mailflow.sqlite")

    store.record(signal())

    assert store.count() == 1
    examples = store.verified_examples()
    assert len(examples) == 1
    assert examples[0].organization_name == "Metal Factory"
    assert examples[0].organization_role == InterlocutorType.FOURNISSEUR
    assert examples[0].category == RoutingCategory.DEMANDE_DE_PRIX


def test_verified_example_is_idempotent_by_mail_id(tmp_path: Path) -> None:
    store = SQLiteLearningStore(tmp_path / "mailflow.sqlite")
    store.record(signal())
    corrected = signal().model_copy(
        update={
            "selected_mail_type": MailType.COMMANDE,
            "selected_target_folder": "Fournisseurs/Commande/Metal Factory",
        }
    )

    store.record(corrected)

    assert store.count() == 2
    examples = store.verified_examples()
    assert len(examples) == 1
    assert examples[0].category == RoutingCategory.COMMANDE


def test_unknown_role_is_not_used_as_verified_ai_example(tmp_path: Path) -> None:
    store = SQLiteLearningStore(tmp_path / "mailflow.sqlite")
    unknown = signal().model_copy(
        update={"selected_interlocutor": InterlocutorType.INCONNU}
    )

    store.record(unknown)

    assert store.count() == 1
    assert store.verified_examples() == []
