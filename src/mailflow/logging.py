from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

REDACTED = "[secret masque]"
# Key-shaped values only: a file named "Risk-analyse.msg" is not a secret.
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|authorization)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{12,}"),
)


def redact_secrets(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Mask key-shaped values in a log message, keeping the rest of the line."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class SecretRedactionFormatter(logging.Formatter):
    """Also mask what the filter cannot see: tracebacks and exception texts."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "mailflow_archivist.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(
        SecretRedactionFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
