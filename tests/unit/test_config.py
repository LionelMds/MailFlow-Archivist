from __future__ import annotations

import json
from pathlib import Path

import pytest

from mailflow.config import (
    AI_MODEL_OPTIONS,
    AppPaths,
    AppSettings,
    load_settings,
    load_settings_with_recovery,
    save_settings,
)
from mailflow.models import AiMode


def test_settings_round_trip_without_api_key(tmp_path: Path) -> None:
    settings = AppSettings(
        paths=AppPaths(data_dir=tmp_path),
        local_projects_root=tmp_path / "Clients",
        selected_year="2025",
        ai_mode=AiMode.DISABLED,
    )

    save_settings(settings)
    loaded = load_settings(tmp_path / "config.json")

    assert loaded.local_projects_root == tmp_path / "Clients"
    assert loaded.selected_year == "2025"
    assert loaded.review_reminder_times == ["09:00", "14:00"]
    assert "openai_api_key" not in (tmp_path / "config.json").read_text(encoding="utf-8")
    assert [path.name for path in tmp_path.iterdir()] == ["config.json"]


def test_default_projects_root_follows_current_user() -> None:
    assert AppSettings().local_projects_root == (
        Path.home() / "OneDrive - Balz Metal Sa" / "Clients"
    )


def test_retired_and_unknown_keys_are_ignored(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "selected_year": "2025",
        "openai_api_key": "sk-never-loaded",
        "client_email_domains": ["gva.ch"],
        "rule_confidence_threshold": 0.8,
        "setting_from_a_newer_version": True,
    }), encoding="utf-8")

    assert load_settings(config_path).selected_year == "2025"


@pytest.mark.parametrize("content", ["{not json", "[]", '{"ai_mode": "unknown"}'])
def test_unreadable_settings_are_set_aside(tmp_path: Path, content: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(content, encoding="utf-8")

    settings, warning = load_settings_with_recovery(config_path)

    assert settings == AppSettings()
    assert warning is not None
    assert not config_path.exists()
    backups = list(tmp_path.glob("config.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].name in warning
    assert backups[0].read_text(encoding="utf-8") == content


def test_readable_settings_load_without_warning(tmp_path: Path) -> None:
    settings = AppSettings(paths=AppPaths(data_dir=tmp_path), selected_year="2024")
    save_settings(settings)

    loaded, warning = load_settings_with_recovery(tmp_path / "config.json")

    assert warning is None
    assert loaded.selected_year == "2024"


def test_settings_default_ai_model_is_astra() -> None:
    assert AppSettings().ai_model == "gpt-6-astra"
    assert AppSettings().openai_timeout_seconds == 60.0
    assert AI_MODEL_OPTIONS[0] == "gpt-6-astra"
    assert "gpt-5.4-mini" in AI_MODEL_OPTIONS
    assert "gpt-5.5" in AI_MODEL_OPTIONS


def test_settings_default_review_reminders_are_workday_friendly() -> None:
    assert AppSettings().review_reminder_times == ["09:00", "14:00"]


def test_legacy_default_model_migrates_to_astra_once(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "ai_model": "gpt-5.4-nano", "openai_timeout_seconds": 25,
        "ai_mode": "disabled", "selected_year": "2025",
    }), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.ai_model == "gpt-6-astra"
    assert settings.openai_timeout_seconds == 60.0
    assert settings.ai_mode == AiMode.DISABLED
    assert settings.selected_year == "2025"
    save_settings(settings, config_path)
    assert load_settings(config_path) == settings

    settings.ai_model = "gpt-5.4-nano"
    save_settings(settings, config_path)
    assert load_settings(config_path).ai_model == "gpt-5.4-nano"


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-4o", "custom-model-id"])
def test_legacy_custom_model_is_preserved(tmp_path: Path, model: str) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "ai_model": model, "openai_timeout_seconds": 25,
    }), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.ai_model == model
    assert settings.openai_timeout_seconds == 25.0


def test_legacy_model_migration_preserves_custom_timeout(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "ai_model": "gpt-5.4-nano", "openai_timeout_seconds": 90,
    }), encoding="utf-8")

    assert load_settings(config_path).openai_timeout_seconds == 90.0


def test_ollama_settings_round_trip_and_legacy_provider(tmp_path: Path) -> None:
    assert AppSettings().ai_provider == "openai"
    settings = AppSettings(
        paths=AppPaths(data_dir=tmp_path), ai_provider="ollama",
        ollama_base_url="http://localhost:11434/", ollama_model="qwen3.5:4b",
        ollama_timeout_seconds=120,
    )

    save_settings(settings)

    assert load_settings(tmp_path / "config.json") == settings
    assert settings.ollama_base_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize("url", [
    "https://ollama.com", "http://192.168.1.2:11434", "http://127.0.0.1.example.com",
    "http://user:pass@localhost:11434", "http://localhost:11434/api", "file:///tmp/ollama",
    "http://localhost:11434?token=secret", "http://localhost:11434#fragment",
    "http://localhost:99999", "http://localhost:0", "http://[invalid",
])
def test_ollama_rejects_nonlocal_or_ambiguous_urls(url: str) -> None:
    with pytest.raises(ValueError, match="adresse HTTP locale"):
        AppSettings(ollama_base_url=url)


def test_ollama_supports_ipv6_loopback() -> None:
    assert AppSettings(ollama_base_url="http://[::1]:11434").ollama_base_url == (
        "http://[::1]:11434"
    )


def test_jev_settings_round_trip_without_key(tmp_path: Path) -> None:
    settings = AppSettings(
        paths=AppPaths(data_dir=tmp_path), ai_provider="jev", jev_model="jev-custom",
    )

    save_settings(settings)

    loaded = load_settings(tmp_path / "config.json")
    assert loaded == settings
    assert loaded.jev_timeout_seconds == 20.0
    assert "api_key" not in (tmp_path / "config.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        AppSettings(jev_model="")


def test_jev_and_openai_keys_use_separate_keyring_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    from types import SimpleNamespace

    from mailflow import config

    store: dict[tuple[str, str], str] = {}
    fake_keyring = SimpleNamespace(
        get_password=lambda service, user: store.get((service, user)),
        set_password=lambda service, user, secret: store.update({(service, user): secret}),
    )
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)

    assert config.get_jev_api_key() is None
    config.set_jev_api_key("ts-key")
    config.set_openai_api_key("sk-key")

    assert config.get_jev_api_key() == "ts-key"
    assert config.get_openai_api_key() == "sk-key"
    assert store == {
        ("mailflow-archivist", "typesafe-jev-api-key"): "ts-key",
        ("mailflow-archivist", "openai-api-key"): "sk-key",
    }


def test_mailbox_sorting_options_round_trip_and_default_for_old_configs(
    tmp_path: Path,
) -> None:
    old_config = tmp_path / "old.json"
    old_config.write_text(json.dumps({"settings_version": 1}), encoding="utf-8")
    defaults = load_settings(old_config)
    settings = AppSettings(
        paths=AppPaths(data_dir=tmp_path),
        mailbox_pending_folder="Tri",
        mailbox_sort_days=0,
        mailbox_read_attachments=False,
        mailbox_suggest_with_jev=True,
    )

    save_settings(settings)
    loaded = load_settings(settings.paths.config_file)

    assert defaults.mailbox_pending_folder == "A CLASSER"
    assert defaults.mailbox_sort_days == 90
    assert defaults.mailbox_read_attachments
    assert not defaults.mailbox_suggest_with_jev
    assert loaded.mailbox_pending_folder == "Tri"
    assert loaded.mailbox_sort_days == 0
    assert not loaded.mailbox_read_attachments
    assert loaded.mailbox_suggest_with_jev
