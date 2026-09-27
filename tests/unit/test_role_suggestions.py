"""Jev suggests a role for unknown companies; only the user writes it to the directory.

A validated role applies at once to the mails, from the answers Jev already gave for
that role, without a new AI call.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest

from mailflow.config import AppPaths, AppSettings
from mailflow.core.app_controller import AppController
from mailflow.core.role_suggestions import RoleSuggestion, suggest_roles
from mailflow.models import (
    AiMailClassification,
    ArchiveDecision,
    ClassificationResult,
    Direction,
    InterlocutorType,
    MailMetadata,
    MailType,
    PreviewAction,
    PreviewRow,
    RoleEstimate,
    RuleClassification,
)
from mailflow.storage.directory_store import SQLiteDirectoryStore
from mailflow.ui.mail_preview import preview_row_to_html

PROJECT = "2025-4893"


def mail(entry_id: str, sender: str) -> MailMetadata:
    return MailMetadata(
        entry_id=entry_id,
        project_number=PROJECT,
        outlook_folder=PROJECT,
        direction=Direction.RECEIVED,
        subject=f"Sujet {entry_id}",
        sender_name="Contact",
        sender_email=sender,
        recipients=["Lionel <lionel@balzmetal.ch>"],
        sent_at=datetime(2026, 5, 6, 10, 30),
    )


def answer(category: str, role: str, confidence: float) -> AiMailClassification:
    return AiMailClassification.model_validate({
        "category": category, "organization_role": role, "organization_name": None,
        "confidence": confidence, "requires_review": confidence < 0.8,
        "short_summary": f"Suivi de commande : {category}",
        "reason": f"Jev : Suivi de commande ({confidence:.0%}).", "evidence": [],
    })


SUPPLIER_ANSWERS = {
    "fournisseur": answer("Commande", "fournisseur", 0.97),
    "client": answer("Correspondance", "client", 1.0),
}


def row(
    tmp_path: Path,
    metadata: MailMetadata,
    probabilities: dict[str, float] | None,
    classifications: dict[str, AiMailClassification] | None = None,
) -> PreviewRow:
    estimate = (
        None if probabilities is None and classifications is None
        else RoleEstimate(
            probabilities=probabilities or {}, classifications=classifications or {},
        )
    )
    return PreviewRow(
        mail=metadata,
        classification=ClassificationResult(
            rule=RuleClassification(confidence=0.0),
            ai=answer("Commande", "inconnu", 0.97),
            role_estimate=estimate,
        ),
        decision=ArchiveDecision(
            mail_id=metadata.entry_id,
            project_number=PROJECT,
            archive=False,
            requires_review=True,
            mail_type=MailType.COMMANDE,
            interlocutor=InterlocutorType.INCONNU,
            target_relative_folder="A verifier",
            target_path=tmp_path / PROJECT,
            confidence=0.9,
            duplicate_status="none",
            reason="Role client/fournisseur a confirmer dans l'annuaire global.",
        ),
        action=PreviewAction.REVIEW,
    )


SUPPLIER = {"fournisseur": 0.96, "client": 0.03, "autre": 0.01}


def test_estimates_are_averaged_per_company(tmp_path: Path) -> None:
    ids = {"a@gyso.ch": 1, "b@gyso.ch": 1, "info@client.ch": 2}
    rows = [
        row(tmp_path, mail("1", "a@gyso.ch"), SUPPLIER),
        row(tmp_path, mail("2", "b@gyso.ch"), {"fournisseur": 0.7, "client": 0.3, "autre": 0}),
        row(tmp_path, mail("3", "info@client.ch"), {"fournisseur": 0.1, "client": 0.6}),
        row(tmp_path, mail("4", "x@unknown.ch"), SUPPLIER),
        row(tmp_path, mail("5", "a@gyso.ch"), None),
    ]

    suggestions = suggest_roles(rows, ids.get)

    assert set(suggestions) == {1, 2}
    gyso = suggestions[1]
    assert gyso.role == InterlocutorType.FOURNISSEUR
    assert gyso.probability == pytest.approx(0.83)
    assert gyso.mail_count == 2
    assert gyso.is_confident
    assert gyso.label == "Fournisseur · 83% · 2 mails"
    client = suggestions[2]
    assert client.role == InterlocutorType.CLIENT
    assert not client.is_confident


def test_neither_client_nor_supplier_is_shown_but_never_applicable() -> None:
    other = RoleSuggestion(organization_id=3, key="autre", probability=0.95, mail_count=1)
    assert other.role is None
    assert not other.is_confident
    assert other.label == "Ni client ni fournisseur · 95% · 1 mail"


def test_mail_preview_shows_the_suggested_role(tmp_path: Path) -> None:
    preview = row(tmp_path, mail("1", "a@gyso.ch"), SUPPLIER)

    html = preview_row_to_html(preview)

    assert "Rôle suggéré :</b> Fournisseur (96%)" in html
    assert "Annuaire" in html


class NoPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def preview(
        self,
        mails: list[MailMetadata],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PreviewRow]:
        self.calls += 1
        return []


def controller_with_rows(tmp_path: Path) -> tuple[AppController, SQLiteDirectoryStore]:
    store = SQLiteDirectoryStore(tmp_path / "mailflow.sqlite")
    gyso = store.add_organization("Gyso AG", domain="gyso.ch")
    store.add_organization("Client SA", domain="client.ch", role=InterlocutorType.CLIENT)
    assert gyso > 0
    (tmp_path / "2025" / PROJECT).mkdir(parents=True)
    app = AppController(
        scan_service=cast(Any, None),
        preview_pipeline=NoPipeline(),
        projects_root=tmp_path,
        report_dir=tmp_path,
        directory_store=store,
    )
    app.preview_rows = [
        row(tmp_path, mail("1", "a@gyso.ch"), SUPPLIER, SUPPLIER_ANSWERS),
        row(tmp_path, mail("2", "info@client.ch"), {"fournisseur": 0.9, "client": 0.1}),
    ]
    return app, store


def test_controller_suggests_only_for_companies_without_business_role(tmp_path: Path) -> None:
    app, store = controller_with_rows(tmp_path)
    gyso_id = store.organization_id_for_email("a@gyso.ch")

    assert gyso_id is not None
    suggestions = app.directory_role_suggestions()

    # Client SA already has a role in the directory: its estimate is not shown.
    assert list(suggestions) == [gyso_id]
    assert suggestions[gyso_id].role == InterlocutorType.FOURNISSEUR
    # Nothing is written to the directory by a suggestion.
    assert store.interlocutor_for_email(PROJECT, "a@gyso.ch") in {None, InterlocutorType.INCONNU}


def test_validated_role_routes_mails_at_once_without_ai(tmp_path: Path) -> None:
    app, store = controller_with_rows(tmp_path)
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    assert gyso_id is not None

    app.set_directory_organization_roles({gyso_id: InterlocutorType.FOURNISSEUR})

    assert store.interlocutor_for_email(PROJECT, "a@gyso.ch") == InterlocutorType.FOURNISSEUR
    routed = app.preview_rows[0]
    assert routed.action == PreviewAction.ARCHIVE
    assert routed.decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert routed.decision.target_relative_folder == "Fournisseurs/Commande/Gyso AG"
    assert routed.decision.confidence == pytest.approx(0.97)
    assert "Role entreprise applique: fournisseur." in routed.decision.reason
    assert routed.classification.ai is not None
    assert routed.classification.ai.organization_role == "fournisseur"
    # The engine was not called again.
    assert cast(NoPipeline, app.preview_pipeline).calls == 0
    assert app.directory_role_suggestions() == {}


def test_client_role_uses_the_client_answer(tmp_path: Path) -> None:
    app, store = controller_with_rows(tmp_path)
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    assert gyso_id is not None

    app.set_directory_organization_role(gyso_id, InterlocutorType.CLIENT)

    routed = app.preview_rows[0]
    assert routed.action == PreviewAction.ARCHIVE
    assert routed.decision.target_relative_folder == "Correspondance/Gyso AG"


def test_refresh_applies_roles_changed_elsewhere_and_keeps_manual_choices(
    tmp_path: Path,
) -> None:
    from mailflow.core.manual_review import MANUAL_DECISION_REASON

    app, store = controller_with_rows(tmp_path)
    manual = row(tmp_path, mail("9", "b@gyso.ch"), SUPPLIER, SUPPLIER_ANSWERS)
    manual = manual.model_copy(update={
        "decision": manual.decision.model_copy(update={
            "reason": MANUAL_DECISION_REASON, "interlocutor": InterlocutorType.FOURNISSEUR,
            "target_relative_folder": "Fournisseurs/Demande de prix/Gyso AG",
        }),
    })
    app.preview_rows.append(manual)
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    assert gyso_id is not None
    # A role saved directly in the directory, outside this controller.
    store.set_organization_role(gyso_id, InterlocutorType.FOURNISSEUR)
    before = app.preview_rows[0].action

    app.refresh_directory_roles()

    assert before == PreviewAction.REVIEW
    assert app.preview_rows[0].action == PreviewAction.ARCHIVE
    assert app.preview_rows[-1].decision.target_relative_folder == (
        "Fournisseurs/Demande de prix/Gyso AG"
    )
    assert app.preview_rows[-1].decision.reason == MANUAL_DECISION_REASON
    assert cast(NoPipeline, app.preview_pipeline).calls == 0


def test_low_confidence_answer_stays_for_review(tmp_path: Path) -> None:
    app, store = controller_with_rows(tmp_path)
    app.preview_rows[0] = row(tmp_path, mail("1", "a@gyso.ch"), SUPPLIER, {
        "fournisseur": answer("Demande de prix", "fournisseur", 0.55),
    })
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    assert gyso_id is not None

    app.set_directory_organization_role(gyso_id, InterlocutorType.FOURNISSEUR)

    assert app.preview_rows[0].action == PreviewAction.REVIEW
    assert app.preview_rows[0].decision.interlocutor == InterlocutorType.FOURNISSEUR
    assert app.preview_rows[0].decision.target_relative_folder == "A verifier"


def show_directory(window: Any, app: Any) -> None:
    window.mailflow_navigation.setCurrentRow(2)
    app.processEvents()


def test_directory_tab_validates_one_suggestion_or_all_confident_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

    from mailflow.ui.main_window import DIRECTORY_SUGGESTION_COLUMN, MainWindow

    qt_app = QApplication.instance() or QApplication([])
    app, store = controller_with_rows(tmp_path)
    store.add_organization("Steel Supply", domain="steel-supply.ch")
    app.preview_rows.append(
        row(tmp_path, mail("3", "info@steel-supply.ch"), SUPPLIER, SUPPLIER_ANSWERS)
    )
    window = cast(Any, MainWindow(AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app))
    show_directory(window, qt_app)
    directory = window.mailflow_directory_table
    names = [directory.item(index, 0).text() for index in range(directory.rowCount())]
    gyso_row = names.index("Gyso AG")
    client_row = names.index("Client SA")

    cell = directory.cellWidget(gyso_row, DIRECTORY_SUGGESTION_COLUMN)
    assert cell.findChild(QLabel).text() == "Fournisseur · 96% · 1 mail"
    assert directory.cellWidget(client_row, DIRECTORY_SUGGESTION_COLUMN) is None
    assert window.mailflow_validate_suggestions_button.isEnabled()
    assert window.mailflow_validate_suggestions_button.text().endswith("(2)")

    cell.findChild(QPushButton).click()
    qt_app.processEvents()
    assert store.interlocutor_for_email(PROJECT, "a@gyso.ch") == InterlocutorType.FOURNISSEUR
    assert directory.cellWidget(gyso_row, DIRECTORY_SUGGESTION_COLUMN) is None
    assert window.mailflow_validate_suggestions_button.text().endswith("(1)")
    assert app.preview_rows[0].action == PreviewAction.ARCHIVE
    assert "1 prêt(s) à archiver" in window.mailflow_directory_status_label.text()

    questions: list[str] = []

    def confirm(*args: Any, **_kwargs: Any) -> Any:
        questions.append(str(args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    window.mailflow_validate_suggestions_button.click()
    qt_app.processEvents()

    assert "Steel Supply → Fournisseur · 96% · 1 mail" in questions[0]
    assert store.interlocutor_for_email(PROJECT, "info@steel-supply.ch") == (
        InterlocutorType.FOURNISSEUR
    )
    assert "sans nouvel appel à l'IA" in questions[0]
    assert app.preview_rows[2].action == PreviewAction.ARCHIVE
    # Roles are applied from the answers already received: no AI call.
    assert cast(NoPipeline, app.preview_pipeline).calls == 0
    assert not window.mailflow_validate_suggestions_button.isEnabled()
    window.close()
    qt_app.processEvents()


def test_refused_confirmation_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from mailflow.ui.main_window import MainWindow

    qt_app = QApplication.instance() or QApplication([])
    app, store = controller_with_rows(tmp_path)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    window = cast(Any, MainWindow(AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app))
    show_directory(window, qt_app)

    window.mailflow_validate_suggestions_button.click()

    assert store.interlocutor_for_email(PROJECT, "a@gyso.ch") in {None, InterlocutorType.INCONNU}
    assert app.preview_rows[0].action == PreviewAction.REVIEW
    window.close()
    qt_app.processEvents()


def test_changing_a_role_keeps_the_directory_scroll_position(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import DIRECTORY_ROLE_COLUMN, MainWindow

    qt_app = QApplication.instance() or QApplication([])
    app, store = controller_with_rows(tmp_path)
    for index in range(80):
        store.add_organization(f"Entreprise {index:02d}", domain=f"entreprise{index:02d}.ch")
    window = cast(Any, MainWindow(AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app))
    window.resize(1200, 800)
    window.show()
    show_directory(window, qt_app)
    directory = window.mailflow_directory_table
    scroll = directory.verticalScrollBar()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    position = scroll.value()
    row_index = directory.rowAt(directory.viewport().height() // 2)
    name = directory.item(row_index, 0).text()
    directory.selectRow(row_index)
    combo = directory.cellWidget(row_index, DIRECTORY_ROLE_COLUMN)
    window.activateWindow()
    combo.setFocus(Qt.FocusReason.MouseFocusReason)
    qt_app.processEvents()

    combo.setCurrentText("fournisseur")
    for _ in range(3):
        qt_app.processEvents()

    assert scroll.value() == position
    assert directory.item(row_index, 0).text() == name
    assert directory.currentRow() == row_index
    assert directory.cellWidget(row_index, DIRECTORY_ROLE_COLUMN).currentText() == "fournisseur"
    window.close()
    qt_app.processEvents()


def test_refresh_roles_button_applies_the_directory_without_ai(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import MainWindow

    qt_app = QApplication.instance() or QApplication([])
    app, store = controller_with_rows(tmp_path)
    window = cast(Any, MainWindow(AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app))
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    assert gyso_id is not None
    store.set_organization_role(gyso_id, InterlocutorType.FOURNISSEUR)
    assert window.mailflow_refresh_roles_button.text() == "Actualiser les rôles des mails"
    assert window.mailflow_refresh_roles_action.text() == "Actualiser les rôles (sans IA)"

    window.mailflow_refresh_roles_button.click()

    assert app.preview_rows[0].action == PreviewAction.ARCHIVE
    assert cast(NoPipeline, app.preview_pipeline).calls == 0
    assert "sans nouvel appel IA" in window.mailflow_logs.toPlainText()
    window.close()
    qt_app.processEvents()


def test_mouse_wheel_over_a_role_list_scrolls_instead_of_changing_it(tmp_path: Path) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    from mailflow.ui.main_window import DIRECTORY_ROLE_COLUMN, MainWindow

    qt_app = QApplication.instance() or QApplication([])
    app, _store = controller_with_rows(tmp_path)
    window = cast(Any, MainWindow(AppSettings(paths=AppPaths(data_dir=tmp_path)), controller=app))
    show_directory(window, qt_app)
    combo = window.mailflow_directory_table.cellWidget(0, DIRECTORY_ROLE_COLUMN)
    before = combo.currentText()
    event = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )

    combo.wheelEvent(event)

    assert combo.currentText() == before
    assert not event.isAccepted()
    window.close()
    qt_app.processEvents()


def test_later_role_changes_keep_folders_renamed_by_the_user(tmp_path: Path) -> None:
    app, store = controller_with_rows(tmp_path)
    store.add_organization("Steel Supply", domain="steel-supply.ch")
    app.preview_rows.append(
        row(tmp_path, mail("3", "info@steel-supply.ch"), SUPPLIER, SUPPLIER_ANSWERS)
    )
    gyso_id = store.organization_id_for_email("a@gyso.ch")
    steel_id = store.organization_id_for_email("info@steel-supply.ch")
    assert gyso_id is not None and steel_id is not None
    app.set_directory_organization_role(gyso_id, InterlocutorType.FOURNISSEUR)
    app.rename_preview_folder("Fournisseurs/Commande/Gyso AG", "Gyso")

    app.set_directory_organization_role(steel_id, InterlocutorType.FOURNISSEUR)
    app.refresh_directory_roles()

    assert app.preview_rows[0].decision.target_relative_folder == "Fournisseurs/Commande/Gyso"
    assert app.preview_rows[2].decision.target_relative_folder == (
        "Fournisseurs/Commande/Steel Supply"
    )
