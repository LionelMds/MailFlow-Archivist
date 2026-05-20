from __future__ import annotations

import re

DEFAULT_EXCERPT_LIMIT = 8000

QUOTE_MARKERS = [
    r"^-{2,}\s*Original Message\s*-{2,}$",
    r"^De\s*:\s+.+$",
    r"^From\s*:\s+.+$",
    r"^On .+ wrote:$",
    r"^Le .+ a ecrit\s*:$",
]
SIGNATURE_MARKERS = [
    r"^--\s*$",
    r"^Cordialement[,]?\s*$",
    r"^Best regards[,]?\s*$",
    r"^Mit freundlichen Grussen[,]?\s*$",
]
DISCLAIMER_MARKERS = [
    r"confidentiality notice",
    r"ce message et toutes les pieces jointes",
    r"this e-mail and any attachments",
    r"disclaimer",
]
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()/-]{7,}\d)(?!\d)")
MULTIPLE_BLANKS_RE = re.compile(r"\n{3,}")


def clean_body(
    body: str,
    *,
    limit: int = DEFAULT_EXCERPT_LIMIT,
    mask_phone_numbers: bool = False,
) -> str:
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    kept: list[str] = []

    for line in lines:
        stripped = line.strip()
        if _matches_any(stripped, QUOTE_MARKERS) or _matches_any(stripped, SIGNATURE_MARKERS):
            break
        if any(marker in stripped.lower() for marker in DISCLAIMER_MARKERS):
            break
        kept.append(line.rstrip())

    cleaned = "\n".join(kept).strip()
    cleaned = MULTIPLE_BLANKS_RE.sub("\n\n", cleaned)
    cleaned = "\n".join(part.strip() for part in cleaned.split("\n"))
    if mask_phone_numbers:
        cleaned = PHONE_RE.sub("[telephone masque]", cleaned)
    return cleaned[:limit].rstrip()


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(re.match(pattern, value, flags=re.IGNORECASE) for pattern in patterns)

