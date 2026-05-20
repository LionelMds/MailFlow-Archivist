from __future__ import annotations

from pathlib import Path

from mailflow.config import AppPaths, AppSettings, load_settings, save_settings
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
    assert "openai_api_key" not in (tmp_path / "config.json").read_text(encoding="utf-8")


def test_settings_default_ai_model_is_fast_low_cost() -> None:
    assert AppSettings().ai_model == "gpt-5.4-nano"
