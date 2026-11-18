from __future__ import annotations

import re
import unicodedata

DEFAULT_EXCERPT_LIMIT = 8000

# Markers are compared with lines reduced to plain letters: "a écrit" and "a ecrit",
# "Grüssen" and "Grussen" are the same marker.
QUOTE_MARKERS = [
    r"^-{2,}\s*Original Message\s*-{2,}$",
    r"^-{2,}\s*Message d'origine\s*-{2,}$",
    r"^-{2,}\s*Forwarded message\s*-{2,}$",
    r"^Debut du message (?:transfere|reexpedie)\s*:$",
    # Any "De :" line ends the excerpt: a project number quoted in an older message
    # must never route the mail, even if this sometimes cuts ordinary text.
    r"^(?:De|From|Von)\s*:\s+.+$",
    r"^On .+ wrote\s*:$",
    r"^Le .+ a ecrit\s*:$",
    r"^Am .+ schrieb .+:$",
]
SIGNATURE_MARKERS = [
    r"^--\s*$",
    r"^Cordialement[,]?\s*$",
    r"^Meilleures salutations[,]?\s*$",
    r"^Best regards[,]?\s*$",
    r"^Mit freundlichen Gru(?:ss|ß)en[,]?\s*$",
    r"^Freundliche Gru(?:ss|ß)e[,]?\s*$",
]
DISCLAIMER_MARKERS = [
    "confidentiality notice",
    "ce message et toutes les pieces jointes",
    "this e-mail and any attachments",
    "disclaimer",
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
        plain = plain_line(line)
        if _matches_any(plain, QUOTE_MARKERS) or _matches_any(plain, SIGNATURE_MARKERS):
            break
        if any(marker in plain.lower() for marker in DISCLAIMER_MARKERS):
            break
        kept.append(line.rstrip())

    cleaned = "\n".join(kept).strip()
    cleaned = MULTIPLE_BLANKS_RE.sub("\n\n", cleaned)
    cleaned = "\n".join(part.strip() for part in cleaned.split("\n"))
    if mask_phone_numbers:
        cleaned = PHONE_RE.sub("[telephone masque]", cleaned)
    return cleaned[:limit].rstrip()


def plain_line(line: str) -> str:
    """The line reduced to plain letters, to compare it whatever its accents."""
    decomposed = unicodedata.normalize("NFKD", line.strip())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(re.match(pattern, value, flags=re.IGNORECASE) for pattern in patterns)
