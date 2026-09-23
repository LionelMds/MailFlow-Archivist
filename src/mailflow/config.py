from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mailflow.models import REVIEW_CONFIDENCE_THRESHOLD, AiMode

APP_NAME = "MailFlow Archivist"
KEYRING_SERVICE = "mailflow-archivist"
KEYRING_OPENAI_USERNAME = "openai-api-key"
DEFAULT_AI_MODEL = "gpt-6-astra"
DEFAULT_OPENAI_TIMEOUT_SECONDS = 60.0
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 180.0
SETTINGS_VERSION = 1
AI_MODEL_OPTIONS = (
    DEFAULT_AI_MODEL,
    "gpt-6-luna",
    "gpt-5.4-nano",
    "gpt-5.4-mini",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-4o-mini",
    "gpt-4o",
)


def _default_data_dir() -> Path:
    try:
        from platformdirs import user_data_path

        return Path(user_data_path(APP_NAME, "Balz Metal Sa"))
    except Exception:
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME


def _default_projects_root() -> Path:
    return Path.home() / "OneDrive - Balz Metal Sa" / "Clients"


def validate_ollama_base_url(value: str) -> str:
    message = "Ollama doit utiliser une adresse HTTP locale (127.0.0.1, localhost ou [::1])."
    try:
        url = urlsplit(value.strip())
        port = url.port
    except ValueError as exc:
        raise ValueError(message) from exc
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or port == 0
    ):
        raise ValueError(message)
    # Pin localhost to loopback without relying on DNS or the hosts file.
    host = "[::1]" if url.hostname == "::1" else "127.0.0.1"
    return f"http://{host}" + (f":{port}" if port is not None else "")


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

    settings_version: int = Field(default=SETTINGS_VERSION, ge=1)
    paths: AppPaths = Field(default_factory=AppPaths)
    local_projects_root: Path = Field(default_factory=_default_projects_root)
    outlook_root_folder: str = "Boite de reception"
    selected_outlook_account: str | None = None
    selected_year: str | None = None
    ai_mode: AiMode = AiMode.ALL
    ai_provider: Literal["openai", "ollama"] = "openai"
    ai_model: str = DEFAULT_AI_MODEL
    openai_timeout_seconds: float = Field(default=DEFAULT_OPENAI_TIMEOUT_SECONDS, gt=0)
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = Field(default=DEFAULT_OLLAMA_MODEL, min_length=1)
    ollama_timeout_seconds: float = Field(default=DEFAULT_OLLAMA_TIMEOUT_SECONDS, gt=0)
    ai_include_body_excerpt: bool = True
    privacy_mask_phone_numbers: bool = False
    review_reminder_times: list[str] = Field(default_factory=lambda: ["09:00", "14:00"])
    decision_confidence_threshold: float = Field(
        default=REVIEW_CONFIDENCE_THRESHOLD, ge=0.0, le=1.0,
    )

    @field_validator("ollama_base_url")
    @classmethod
    def local_ollama_url(cls, value: str) -> str:
        return validate_ollama_base_url(value)


def load_settings(path: Path | None = None) -> AppSettings:
    base = AppSettings()
    config_path = path or base.paths.config_file
    if not config_path.exists():
        return base
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Le fichier de reglages ne contient pas un objet JSON.")
    # Drop retired or future keys (e.g. after a downgrade) instead of rejecting the file.
    raw = {key: value for key, value in raw.items() if key in AppSettings.model_fields}
    if raw.get("settings_version", 0) == 0:
        # Only replace the former default. Saving the version makes this migration
        # one-time and lets users subsequently select the legacy model explicitly.
        if raw.get("ai_model", "gpt-5.4-nano") == "gpt-5.4-nano":
            raw["ai_model"] = DEFAULT_AI_MODEL
            if raw.get("openai_timeout_seconds", 25.0) == 25.0:
                raw["openai_timeout_seconds"] = DEFAULT_OPENAI_TIMEOUT_SECONDS
        raw["settings_version"] = SETTINGS_VERSION
    if raw.get("ai_mode") == AiMode.AMBIGUOUS_ONLY.value:
        raw["ai_mode"] = AiMode.ALL.value
    return AppSettings.model_validate(raw)


def load_settings_with_recovery(path: Path | None = None) -> tuple[AppSettings, str | None]:
    """Load settings; set an unreadable file aside and start from defaults.

    Returns the settings and, when recovery happened, a message for the user.
    """
    config_path = path or AppSettings().paths.config_file
    try:
        return load_settings(config_path), None
    except (OSError, ValueError) as exc:
        # json.JSONDecodeError and pydantic.ValidationError are both ValueError.
        reason = type(exc).__name__
    backup = config_path.with_name(
        f"{config_path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{config_path.suffix}"
    )
    try:
        config_path.replace(backup)
    except OSError:
        return AppSettings(), (
            f"Les reglages n'ont pas pu etre lus ({reason}) et le fichier n'a pas pu "
            f"etre mis de cote : {config_path}. Les reglages par defaut sont utilises."
        )
    return AppSettings(), (
        f"Les reglages n'ont pas pu etre lus ({reason}). Le fichier a ete conserve sous "
        f"{backup.name} et les reglages par defaut sont utilises."
    )


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    config_path = path or settings.paths.config_file
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = settings.model_dump(mode="json")
    data.pop("openai_api_key", None)
    content = json.dumps(data, ensure_ascii=True, indent=2)
    # Write a sibling file, then swap it in, so a crash never leaves a truncated config.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=config_path.parent,
        prefix=f".{config_path.name}.", suffix=".tmp", delete=False,
    ) as stream:
        temp_path = Path(stream.name)
        try:
            stream.write(content)
        except BaseException:
            stream.close()
            temp_path.unlink(missing_ok=True)
            raise
    try:
        temp_path.replace(config_path)
    finally:
        temp_path.unlink(missing_ok=True)


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
