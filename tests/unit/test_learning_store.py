from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mailflow.models import InterlocutorType, MailType, ManualLearningSignal
from mailflow.storage.learning_store import SQLiteLearningStore


def test_learning_store_records_manual_signal(tmp_path: Path) -> None:
    store = SQLiteLearningStore(tmp_path / "mailflow.sqlite")
    signal = ManualLearningSignal(
        mail_id="ENTRY-1",
        project_number="2025-4893",
        subject="Offerte",
        selected_mail_type=MailType.DEVIS,
        selected_interlocutor=InterlocutorType.FOURNISSEUR,
        selected_target_folder="DEMANDE DE PRIX",
        learning_term="Offerte",
        misleading_term="newsletter",
        manual_required=False,
        created_at=datetime(2026, 5, 6, 10, 30),
    )

    store.record(signal)

    assert store.count() == 1
    assert store.misleading_terms()[0].term == "newsletter"


def test_learning_store_returns_learned_rules_and_ignores_manual_required(tmp_path: Path) -> None:
    store = SQLiteLearningStore(tmp_path / "mailflow.sqlite")
    store.record(
        ManualLearningSignal(
            mail_id="ENTRY-1",
            project_number="2025-4893",
            subject="Offerte",
            selected_mail_type=MailType.DEVIS,
            selected_interlocutor=InterlocutorType.FOURNISSEUR,
            selected_target_folder="DEMANDE DE PRIX",
            learning_term="Offerte",
            misleading_term=None,
            manual_required=False,
            created_at=datetime(2026, 5, 6, 10, 30),
        )
    )
    store.record(
        ManualLearningSignal(
            mail_id="ENTRY-2",
            project_number="2025-4893",
            subject="Cas humain",
            selected_mail_type=MailType.A_VERIFIER,
            selected_interlocutor=InterlocutorType.INCONNU,
            selected_target_folder="A verifier",
            learning_term=None,
            misleading_term="offre",
            manual_required=True,
            created_at=datetime(2026, 5, 6, 10, 31),
        )
    )

    learned_rules = store.learned_rules()

    assert len(learned_rules) == 1
    assert learned_rules[0].term == "Offerte"
    assert store.misleading_terms()[0].term == "offre"
