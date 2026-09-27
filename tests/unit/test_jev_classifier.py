from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from mailflow.classifier.ai_classifier import _connection_test_mail
from mailflow.classifier.jev_classifier import (
    CLIENT_PHASES,
    SUPPLIER_PHASES,
    UNKNOWN_ROLE_PHASES,
    JevClassifier,
    JevError,
)
from mailflow.classifier.pipeline import ClassificationPipeline
from mailflow.classifier.prompt import build_ai_payload
from mailflow.config import AppSettings
from mailflow.core.app_controller import build_ai_classifier
from mailflow.core.project_digest import build_project_digest
from mailflow.models import Direction, InterlocutorType, MailMetadata, PreviewAction

SECRET = "ts-secret-key-123"


def context(role: str, name: str = "Metal Factory", *, locked: bool = True) -> dict[str, Any]:
    return {
        "counterparty": {
            "primary_email": "sales@metal.test",
            "organization_name": name,
            "project_role": role,
            "organization_locked_by_directory": locked,
            "source": "sender",
        },
        "recent_company_history": [],
        "verified_manual_examples": [],
    }


def jev_response(probabilities: dict[str, float], **extra: Any) -> dict[str, Any]:
    choice = max(probabilities, key=lambda name: probabilities[name])
    return {
        "model": "jev-2026-09-15",
        "answers": {
            "phase": {
                "type": "choice",
                "choice": choice,
                "confidence": probabilities[choice],
                "probabilities": probabilities,
            },
        },
        "usage": {"input_tokens": 850, "output_tokens": 3},
        **extra,
    }


def spread(phases: dict[str, Any], **chosen: float) -> dict[str, float]:
    rest = (1.0 - sum(chosen.values())) / (len(phases) - len(chosen))
    return {name: chosen.get(name, rest) for name in phases}


def classifier_for(
    handler: Any, *, sleeps: list[float] | None = None, api_key: str = SECRET,
) -> tuple[JevClassifier, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    classifier = JevClassifier(
        api_key=api_key, client=client,
        sleep=(sleeps.append if sleeps is not None else lambda _delay: None),
    )
    return classifier, client


def supplier_mail() -> MailMetadata:
    return MailMetadata(
        entry_id="MAIL-1",
        project_number="2025-4893",
        outlook_folder="2025-4893",
        direction=Direction.RECEIVED,
        subject="Confirmation de votre commande 4521",
        sender_name="Metal Factory",
        sender_email="sales@metal.test",
        recipients=["lionel@balzmetal.ch"],
        sent_at=datetime(2026, 3, 12, 9, 30),
        body_excerpt="Nous confirmons la livraison le 20 mars. Tel +41 22 123 45 67.",
    )


def test_request_sends_mail_payload_and_supplier_phases_only() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=jev_response(
            spread(SUPPLIER_PHASES, suivi_commande=0.9),
        ))

    mail = supplier_mail()
    known = context("fournisseur")
    classifier, client = classifier_for(handle)
    with client:
        classifier.classify(mail, privacy_mask_phone_numbers=True, known_context=known)

    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    assert body["model"] == "jev-latest"
    # The same data as the other engines: cleaned body, masked phone numbers, context.
    assert body["state"] == build_ai_payload(
        mail, include_body=True, privacy_mask_phone_numbers=True, known_context=known,
    )
    assert "+41 22 123 45 67" not in request.content.decode()
    question = body["questions"]["phase"]
    assert question["type"] == "choice"
    assert "FOURNISSEUR" in question["instructions"]
    assert set(question["criteria"]) == set(SUPPLIER_PHASES)
    assert all(question["criteria"].values())


def test_supplier_phases_add_up_to_the_category_confidence() -> None:
    probabilities = {
        "consultation": 0.02, "offre_fournisseur": 0.03, "commande": 0.10,
        "suivi_commande": 0.70, "facture": 0.05, "reclamation": 0.10,
    }
    classifier, client = classifier_for(
        lambda _request: httpx.Response(200, json=jev_response(probabilities)),
    )
    with client:
        result = classifier.classify(supplier_mail(), known_context=context("fournisseur"))

    assert result.category == "Commande"
    assert result.confidence == pytest.approx(0.95)
    assert result.requires_review is False
    assert result.organization_role == "fournisseur"
    assert result.organization_name == "Metal Factory"
    assert result.short_summary == "Suivi de commande : Confirmation de votre commande 4521"
    assert result.reason == "Jev : Suivi de commande (70%). Commande 95%, Demande de prix 5%."
    # An unlikely problem must not reach the text searched by the project digest.
    assert "clamation" not in result.reason
    assert result.evidence == []
    assert classifier.last_usage == (850, 3)
    assert classifier.last_served_model == "jev-2026-09-15"


def test_uncertain_supplier_phase_goes_to_review() -> None:
    probabilities = spread(SUPPLIER_PHASES, consultation=0.30, offre_fournisseur=0.30)
    classifier, client = classifier_for(
        lambda _request: httpx.Response(200, json=jev_response(probabilities)),
    )
    with client:
        result = classifier.classify(supplier_mail(), known_context=context("fournisseur"))

    assert result.category == "Demande de prix"
    assert result.confidence == pytest.approx(0.60)
    assert result.requires_review is True


def test_client_is_always_correspondance_and_jev_only_describes_it() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=jev_response(
            spread(CLIENT_PHASES, commande_client=0.55),
        ))

    classifier, client = classifier_for(handle)
    with client:
        result = classifier.classify(supplier_mail(), known_context=context("client", "AIG"))

    assert set(requests[0]["questions"]["phase"]["criteria"]) == set(CLIENT_PHASES)
    assert result.category == "Correspondance"
    assert result.organization_role == "client"
    assert result.confidence == 1.0
    assert result.requires_review is False
    assert result.short_summary.startswith("Commande client : ")
    assert "Correspondance imposée" in result.reason


@pytest.mark.parametrize("known_context", [None, context("inconnu", locked=False)])
def test_unknown_role_offers_every_category_and_requires_review(
    known_context: dict[str, Any] | None,
) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=jev_response(
            spread(UNKNOWN_ROLE_PHASES, echange_client=0.94),
        ))

    classifier, client = classifier_for(handle)
    with client:
        result = classifier.classify(supplier_mail(), known_context=known_context)

    assert set(requests[0]["questions"]["phase"]["criteria"]) == set(UNKNOWN_ROLE_PHASES)
    assert result.category == "Correspondance"
    assert result.organization_role == "inconnu"
    assert result.organization_name is None
    assert result.requires_review is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "Clé Jev refusée"),
        (403, "Accès Jev refusé"),
        (404, "Modèle Jev introuvable"),
        (422, "format de la requête"),
        (302, "code 302"),
    ],
)
def test_http_errors_are_local_messages_without_server_text(status: int, expected: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        # Validation errors echo the rejected input, which contains the mail.
        return httpx.Response(
            status, headers={"location": "https://elsewhere.test/"},
            json={"detail": [{"msg": "invalid", "input": "PRIVATE_MAIL_BODY " + SECRET}]},
        )

    classifier, client = classifier_for(handle)
    with client, pytest.raises(JevError) as caught:
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))

    assert expected in str(caught.value)
    assert "PRIVATE_MAIL_BODY" not in str(caught.value)
    assert SECRET not in str(caught.value)


def test_transient_failure_is_retried_once_with_bounded_delay() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, headers={"retry-after": "120"})
        return httpx.Response(200, json=jev_response(spread(SUPPLIER_PHASES, commande=0.9)))

    classifier, client = classifier_for(handle, sleeps=sleeps)
    with client:
        result = classifier.classify(supplier_mail(), known_context=context("fournisseur"))

    assert result.category == "Commande"
    assert len(calls) == 2
    assert sleeps == [5.0]


def test_rate_limit_after_retry_leaves_mail_for_review() -> None:
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"retry-after-ms": "250"})

    sleeps: list[float] = []
    classifier, client = classifier_for(handle, sleeps=sleeps)
    with client, pytest.raises(JevError, match="Limite Jev atteinte"):
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))
    assert len(calls) == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout, "Délai Jev dépassé"),
        (httpx.ConnectError, "API Jev injoignable"),
    ],
)
def test_network_failures_are_retried_then_reported(
    error: type[httpx.TransportError], expected: str,
) -> None:
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise error("PRIVATE_MAIL_BODY", request=request)

    classifier, client = classifier_for(handle)
    with client, pytest.raises(JevError) as caught:
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))
    assert expected in str(caught.value)
    assert "PRIVATE_MAIL_BODY" not in str(caught.value)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "probabilities",
    [
        {"consultation": 1.0},
        {**spread(SUPPLIER_PHASES), "correspondance": 0.0},
        {**spread(SUPPLIER_PHASES), "commande": True},
        {**spread(SUPPLIER_PHASES), "commande": "0.5"},
        {**spread(SUPPLIER_PHASES), "commande": float("nan")},
        {name: 0.5 for name in SUPPLIER_PHASES},
    ],
)
def test_invalid_answers_are_rejected(probabilities: dict[str, Any]) -> None:
    response = jev_response({"consultation": 1.0})
    response["answers"]["phase"]["probabilities"] = probabilities
    # Encoded by hand: Python's JSON parser accepts NaN, which httpx refuses to write.
    content = json.dumps(response).encode()
    classifier, client = classifier_for(lambda _request: httpx.Response(200, content=content))
    with client, pytest.raises(JevError, match="format attendu"):
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": {}},
        {"answers": {"phase": {"type": "noul", "noul": 0.9}}},
        [],
    ],
)
def test_unexpected_response_shapes_are_rejected(payload: Any) -> None:
    classifier, client = classifier_for(lambda _request: httpx.Response(200, json=payload))
    with client, pytest.raises(JevError):
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))


@pytest.mark.parametrize("api_key", ["", "  ", "clé-accentuée", "two words"])
def test_missing_or_invalid_key_never_calls_the_api(api_key: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("No request without a usable key")

    classifier, client = classifier_for(handle, api_key=api_key)
    with client, pytest.raises(JevError, match=r"clé Jev|Aucune clé"):
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))


def test_connection_check_reports_served_model_and_hides_key() -> None:
    requests: list[dict[str, Any]] = []

    def ok(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=jev_response(
            spread(UNKNOWN_ROLE_PHASES, consultation=0.9),
        ))

    classifier, client = classifier_for(ok)
    with client:
        check = classifier.check_connection()
    assert check.ok
    assert check.message == "Connexion Jev OK (jev-2026-09-15 : Demande de prix, 92%)."
    # Only the fictitious test mail is sent, without body.
    assert requests[0]["state"]["subject"] == _connection_test_mail().subject
    assert requests[0]["state"]["body_excerpt"] == ""

    classifier, client = classifier_for(lambda _request: httpx.Response(401, text=SECRET))
    with client:
        check = classifier.check_connection()
    assert not check.ok
    assert "Clé Jev refusée" in check.message
    assert SECRET not in check.message


class SupplierDirectory:
    def organization_name_for_email(self, email: str) -> str | None:
        return "Metal Factory" if email == "sales@metal.test" else None

    def interlocutor_for_email(self, project_number: str, email: str) -> InterlocutorType | None:
        return InterlocutorType.FOURNISSEUR if email == "sales@metal.test" else None


def test_pipeline_routes_supplier_order_with_jev(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    classifier, client = classifier_for(
        lambda _request: httpx.Response(200, json=jev_response(
            spread(SUPPLIER_PHASES, suivi_commande=0.9),
        )),
    )
    with client:
        pipeline = ClassificationPipeline(
            projects_root=tmp_path, ai_classifier=classifier,
            organization_directory=SupplierDirectory(),
        )
        row = pipeline.preview([supplier_mail()])[0]

    assert row.action == PreviewAction.ARCHIVE
    assert row.decision.target_relative_folder == "Fournisseurs/Commande/Metal Factory"
    digest = build_project_digest([row])
    assert any("Suivi de commande" in point for point in digest.order_points)


def test_pipeline_keeps_mail_for_review_when_jev_fails(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    classifier, client = classifier_for(lambda _request: httpx.Response(500, text=SECRET))
    with client:
        pipeline = ClassificationPipeline(
            projects_root=tmp_path, ai_classifier=classifier,
            organization_directory=SupplierDirectory(),
        )
        row = pipeline.preview_one(supplier_mail())

    assert row.action == PreviewAction.REVIEW
    assert row.classification.ai is None
    assert row.classification.ai_error == "Service Jev indisponible : relancez l'analyse plus tard."
    assert SECRET not in row.model_dump_json()
    assert "OpenAI" not in row.decision.reason


def test_jev_factory_reads_only_the_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> str:
        pytest.fail("Jev must not read the OpenAI key")

    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", forbidden)
    monkeypatch.setattr("mailflow.core.app_controller.get_jev_api_key", lambda: SECRET)
    settings = AppSettings(ai_provider="jev", jev_model="jev-custom", jev_timeout_seconds=7.0)

    classifier = build_ai_classifier(settings)

    assert isinstance(classifier, JevClassifier)
    assert classifier._model == "jev-custom"
    assert classifier._timeout_seconds == 7.0
    monkeypatch.setattr("mailflow.core.app_controller.get_jev_api_key", lambda: None)
    assert build_ai_classifier(settings) is None


def test_other_engines_never_read_the_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> str:
        pytest.fail("Only the Jev engine may read the Jev key")

    monkeypatch.setattr("mailflow.core.app_controller.get_jev_api_key", forbidden)
    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", lambda: "sk-test")
    build_ai_classifier(AppSettings())
    build_ai_classifier(AppSettings(ai_provider="ollama"))


def choice_answer(probabilities: dict[str, float]) -> dict[str, Any]:
    return {
        "type": "choice",
        "choice": max(probabilities, key=lambda name: probabilities[name]),
        "confidence": max(probabilities.values()),
        "probabilities": probabilities,
    }


def unknown_company_response() -> dict[str, Any]:
    response = jev_response(spread(UNKNOWN_ROLE_PHASES, suivi_commande=0.9))
    response["answers"].update({
        "role": choice_answer({"fournisseur": 0.94, "client": 0.04, "autre": 0.02}),
        "phase_si_fournisseur": choice_answer(spread(SUPPLIER_PHASES, suivi_commande=0.92)),
        "phase_si_client": choice_answer(spread(CLIENT_PHASES, execution=0.7)),
    })
    return response


def test_unknown_company_gets_role_estimate_and_answers_for_each_role() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=unknown_company_response())

    classifier, client = classifier_for(handle)
    with client:
        result = classifier.classify(
            supplier_mail(), known_context=context("inconnu", "Gyso AG"),
        )

    questions = requests[0]["questions"]
    assert set(questions) == {"phase", "role", "phase_si_fournisseur", "phase_si_client"}
    assert set(questions["role"]["criteria"]) == {"fournisseur", "client", "autre"}
    assert "Hypothèse" in questions["phase_si_fournisseur"]["instructions"]
    assert set(questions["phase_si_fournisseur"]["criteria"]) == set(SUPPLIER_PHASES)
    assert set(questions["phase_si_client"]["criteria"]) == set(CLIENT_PHASES)
    # The displayed answer is unchanged: the role stays unknown until validated.
    assert result.organization_role == "inconnu"
    assert result.requires_review is True
    estimate = classifier.last_role_estimate
    assert estimate is not None
    assert estimate.probabilities == pytest.approx(
        {"fournisseur": 0.94, "client": 0.04, "autre": 0.02}
    )
    supplier = estimate.classifications["fournisseur"]
    assert supplier.category == "Commande"
    assert supplier.organization_role == "fournisseur"
    assert supplier.confidence == pytest.approx(0.92 + 0.08 / 5 * 3)
    assert supplier.organization_name == "Gyso AG"
    assert estimate.classifications["client"].category == "Correspondance"


def test_known_role_asks_a_single_question() -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=jev_response(spread(SUPPLIER_PHASES, commande=0.9)))

    classifier, client = classifier_for(handle)
    with client:
        classifier.classify(supplier_mail(), known_context=context("fournisseur"))

    assert set(requests[0]["questions"]) == {"phase"}
    assert classifier.last_role_estimate is None


def test_invalid_optional_answers_do_not_block_the_classification() -> None:
    response = unknown_company_response()
    response["answers"]["role"]["probabilities"] = {"fournisseur": 2.0}
    del response["answers"]["phase_si_client"]
    classifier, client = classifier_for(lambda _request: httpx.Response(200, json=response))
    with client:
        result = classifier.classify(supplier_mail(), known_context=context("inconnu"))

    assert result.category == "Commande"
    estimate = classifier.last_role_estimate
    assert estimate is not None
    assert estimate.probabilities == {}
    assert set(estimate.classifications) == {"fournisseur"}


def test_pipeline_keeps_the_estimate_only_for_an_unknown_company(tmp_path: Path) -> None:
    (tmp_path / "2025" / "2025-4893").mkdir(parents=True)
    classifier, client = classifier_for(
        lambda _request: httpx.Response(200, json=unknown_company_response()),
    )
    with client:
        pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=classifier)
        unknown = pipeline.preview_one(supplier_mail())
    assert unknown.classification.role_estimate is not None
    assert unknown.action == PreviewAction.REVIEW

    classifier, client = classifier_for(
        lambda _request: httpx.Response(200, json=jev_response(
            spread(SUPPLIER_PHASES, commande=0.9),
        )),
    )
    with client:
        pipeline = ClassificationPipeline(
            projects_root=tmp_path, ai_classifier=classifier,
            organization_directory=SupplierDirectory(),
        )
        known = pipeline.preview_one(supplier_mail())
    assert known.classification.role_estimate is None
