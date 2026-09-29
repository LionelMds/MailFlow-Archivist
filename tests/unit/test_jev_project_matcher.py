from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx
import pytest

from mailflow.classifier.jev_classifier import JevClassifier
from mailflow.classifier.jev_project_matcher import (
    NO_PROJECT_OPTION,
    PROJECT_QUESTION,
    JevProjectMatcher,
    project_option_key,
)
from mailflow.config import AppSettings
from mailflow.core import app_controller
from mailflow.core.app_controller import build_project_suggester
from mailflow.core.mailbox_sorting import (
    ProjectCandidate,
    ProjectSuggestionError,
)
from mailflow.models import Direction, MailMetadata

SECRET = "ts-secret-key-123"
CANDIDATES = [
    ProjectCandidate("2025-4893", "2025-4893 Villa Dupont", ("Fournisseur SA",)),
    ProjectCandidate("2025-5012", "2025-5012 Hangar", ()),
]


def mail() -> MailMetadata:
    return MailMetadata(
        entry_id="MAIL-1",
        project_number="",
        outlook_folder="Boîte de réception",
        direction=Direction.RECEIVED,
        subject="Livraison des profilés mardi",
        sender_name="Fournisseur SA",
        sender_email="achat@fournisseur.test",
        recipients=["lionel@balzmetal.ch"],
        sent_at=datetime(2026, 9, 28, 10, 30),
        attachment_names=["BL 4411.pdf"],
        body_excerpt="Livraison pour la villa Dupont, tel 079 555 12 34.",
    )


def response(probabilities: dict[str, float]) -> dict[str, Any]:
    choice = max(probabilities, key=lambda name: probabilities[name])
    return {
        "model": "jev-2026-09-15",
        "answers": {
            PROJECT_QUESTION: {
                "type": "choice",
                "choice": choice,
                "confidence": probabilities[choice],
                "probabilities": probabilities,
            },
        },
    }


def matcher_for(
    handler: Any, **options: Any,
) -> tuple[JevProjectMatcher, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        result: httpx.Response = handler(request)
        return result

    client = httpx.Client(transport=httpx.MockTransport(record))
    classifier = JevClassifier(api_key=SECRET, client=client, sleep=lambda _delay: None)
    return JevProjectMatcher(classifier, **options), sent


def answer(probabilities: dict[str, float]) -> Any:
    return lambda _request: httpx.Response(200, json=response(probabilities))


def test_jev_chooses_among_known_projects_and_none() -> None:
    matcher, sent = matcher_for(answer({
        "projet_2025_4893": 0.86, "projet_2025_5012": 0.04, NO_PROJECT_OPTION: 0.10,
    }))

    suggestion = matcher.suggest(mail(), CANDIDATES)

    assert suggestion is not None
    assert suggestion.project_number == "2025-4893"
    assert suggestion.probability == pytest.approx(0.86)
    question = sent[0]["questions"][PROJECT_QUESTION]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {
        "projet_2025_4893", "projet_2025_5012", NO_PROJECT_OPTION,
    }
    assert "2025-4893 Villa Dupont" in question["criteria"]["projet_2025_4893"]
    assert "Fournisseur SA" in question["criteria"]["projet_2025_4893"]
    state = sent[0]["state"]
    assert state["subject"] == "Livraison des profilés mardi"
    assert "project_number" not in state and "known_context" not in state


def test_privacy_settings_apply_to_the_mail_sent_to_jev() -> None:
    matcher, sent = matcher_for(
        answer({"projet_2025_4893": 0.9, "projet_2025_5012": 0.05, NO_PROJECT_OPTION: 0.05}),
        privacy_mask_phone_numbers=True,
    )
    matcher.suggest(mail(), CANDIDATES)
    without_body, sent_without_body = matcher_for(
        answer({"projet_2025_4893": 0.9, "projet_2025_5012": 0.05, NO_PROJECT_OPTION: 0.05}),
        include_body=False,
    )
    without_body.suggest(mail(), CANDIDATES)

    assert "079 555" not in sent[0]["state"]["body_excerpt"]
    assert sent_without_body[0]["state"]["body_excerpt"] == ""


@pytest.mark.parametrize(
    "probabilities",
    [
        {"projet_2025_4893": 0.40, "projet_2025_5012": 0.35, NO_PROJECT_OPTION: 0.25},
        {"projet_2025_4893": 0.45, "projet_2025_5012": 0.05, NO_PROJECT_OPTION: 0.50},
    ],
)
def test_uncertain_answers_give_no_suggestion(probabilities: dict[str, float]) -> None:
    matcher, _sent = matcher_for(answer(probabilities))

    assert matcher.suggest(mail(), CANDIDATES) is None


def test_no_candidate_means_no_call() -> None:
    matcher, sent = matcher_for(answer({NO_PROJECT_OPTION: 1.0}))

    assert matcher.suggest(mail(), []) is None
    assert sent == []


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": "mail content echoed"}, "Clé Jev refusée"),
        (200, {"answers": {}}, "format attendu"),
    ],
)
def test_jev_errors_become_local_suggestion_errors(
    status: int, body: dict[str, Any], message: str,
) -> None:
    matcher, _sent = matcher_for(lambda _request: httpx.Response(status, json=body))

    with pytest.raises(ProjectSuggestionError, match=message) as error:
        matcher.suggest(mail(), CANDIDATES)

    assert "echoed" not in str(error.value)


def test_project_option_keys_are_identifiers() -> None:
    assert project_option_key("2025-4893") == "projet_2025_4893"


def test_suggester_is_built_only_with_a_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = AppSettings(
        jev_model="jev-2026-09-15", ai_include_body_excerpt=False,
        privacy_mask_phone_numbers=True,
    )
    monkeypatch.setattr(app_controller, "get_jev_api_key", lambda: None)
    assert build_project_suggester(settings) is None

    monkeypatch.setattr(app_controller, "get_jev_api_key", lambda: SECRET)
    matcher = build_project_suggester(settings)

    assert matcher is not None
    assert not matcher.include_body
    assert matcher.privacy_mask_phone_numbers
    assert matcher.classifier._model == "jev-2026-09-15"
