from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mailflow.classifier.ai_classifier import AiClassifier, _connection_test_mail
from mailflow.classifier.ollama_classifier import OllamaClassifier
from mailflow.classifier.pipeline import ClassificationPipeline
from mailflow.config import AppSettings
from mailflow.core.app_controller import build_ai_classifier
from mailflow.models import AiMode, PreviewAction


def test_ollama_factory_never_reads_openai_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_key_lookup() -> str:
        pytest.fail("Ollama must not access OpenAI credentials")

    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", fail_key_lookup)

    classifier = build_ai_classifier(AppSettings(ai_provider="ollama"))

    assert isinstance(classifier, OllamaClassifier)


def test_disabled_ai_does_not_access_either_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_key_lookup() -> str:
        pytest.fail("Disabled AI must not access credentials")

    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", fail_key_lookup)

    assert build_ai_classifier(AppSettings(ai_mode=AiMode.DISABLED)) is None
    assert build_ai_classifier(AppSettings(ai_provider="ollama", ai_mode=AiMode.DISABLED)) is None


def test_existing_openai_configuration_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", lambda: "test")
    assert isinstance(build_ai_classifier(AppSettings()), AiClassifier)
    monkeypatch.setattr("mailflow.core.app_controller.get_openai_api_key", lambda: None)
    assert build_ai_classifier(AppSettings()) is None


def test_ollama_unavailable_keeps_mail_for_review_without_api_fallback(tmp_path: Path) -> None:
    mail = _connection_test_mail()
    (tmp_path / "2026" / mail.project_number).mkdir(parents=True)

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        raise httpx.ConnectError("private mail secret", request=request)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        classifier = OllamaClassifier(client=client)
        pipeline = ClassificationPipeline(projects_root=tmp_path, ai_classifier=classifier)

        row = pipeline.preview_one(mail)

    assert row.action == PreviewAction.REVIEW
    assert row.classification.ai is None
    assert row.classification.ai_error is not None
    assert "Ollama" in row.classification.ai_error
    assert "private" not in row.model_dump_json()
    assert "OpenAI" not in row.classification.ai_error
