from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mailflow.models import AiMode

APP_NAME = "MailFlow Archivist"
KEYRING_SERVICE = "mailflow-archivist"
KEYRING_OPENAI_USERNAME = "openai-api-key"


def _default_data_dir() -> Path:
    try:
        from platformdirs import user_data_path

        return Path(user_data_path(APP_NAME, "Balz Metal Sa"))
    except Exception:
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME


class AppPaths(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    data_dir: Path = Field(default_factory=_default_data_dir)

    @property
    def config_file(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def sqlite_file(self) -> Path:
        return self.data_dir / "mailflow_archivist.sqlite"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"


class AppSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    paths: AppPaths = Field(default_factory=AppPaths)
    local_projects_root: Path = Path(r"C:\Users\Lionel\OneDrive - Balz Metal Sa\Clients")
    outlook_root_folder: str = "Boite de reception"
    selected_outlook_account: str | None = None
    selected_year: str | None = None
    ai_mode: AiMode = AiMode.AMBIGUOUS_ONLY
    ai_model: str = "gpt-4o-mini"
    ai_include_body_excerpt: bool = True
    privacy_mask_phone_numbers: bool = False
    rule_confidence_threshold: float = 0.80
    decision_confidence_threshold: float = 0.80


def load_settings(path: Path | None = None) -> AppSettings:
    base = AppSettings()
    config_path = path or base.paths.config_file
    if not config_path.exists():
        return base
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    raw.pop("openai_api_key", None)
    return AppSettings.model_validate(raw)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    config_path = path or settings.paths.config_file
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = settings.model_dump(mode="json")
    data.pop("openai_api_key", None)
    config_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def get_openai_api_key() -> str | None:
    try:
        import keyring

        password = keyring.get_password(KEYRING_SERVICE, KEYRING_OPENAI_USERNAME)
        return str(password) if password is not None else None
    except Exception:
        return None


def set_openai_api_key(api_key: str) -> None:
    try:
        import keyring
    except Exception as exc:
        msg = "keyring is required to store the OpenAI API key"
        raise RuntimeError(msg) from exc
    keyring.set_password(KEYRING_SERVICE, KEYRING_OPENAI_USERNAME, api_key)
