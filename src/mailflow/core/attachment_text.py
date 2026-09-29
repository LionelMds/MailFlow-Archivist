"""Read the text of common attachment formats to look for a project number.

Only formats readable without Office are supported: PDF (pypdf), the Office Open XML
files (Word, Excel, PowerPoint) and plain text. A damaged, protected or oversized file
simply yields no text; it never stops the mailbox analysis.
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from pathlib import Path

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_TEXT_CHARS = 500_000
# A zip archive declaring more than this once uncompressed is not read (zip bomb).
MAX_UNCOMPRESSED_OFFICE_BYTES = 60 * 1024 * 1024

PDF_EXTENSIONS = frozenset({".pdf"})
PLAIN_TEXT_EXTENSIONS = frozenset({".txt", ".csv", ".htm", ".html", ".xml", ".eml"})
OFFICE_PARTS = {
    ".docx": ("word/document.xml", "word/header", "word/footer", "docProps/core.xml"),
    ".docm": ("word/document.xml", "word/header", "word/footer", "docProps/core.xml"),
    ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/sheet", "docProps/core.xml"),
    ".xlsm": ("xl/sharedStrings.xml", "xl/worksheets/sheet", "docProps/core.xml"),
    ".pptx": ("ppt/slides/slide", "docProps/core.xml"),
}
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | PLAIN_TEXT_EXTENSIONS | frozenset(OFFICE_PARTS)

# Paragraphs, table cells and shared strings end a line; other tags are dropped so
# that a number split across formatting runs ("2025-" + "4893") stays whole.
_BLOCK_END_RE = re.compile(r"</(?:w:p|w:tc|a:p|si|c|row|dc:title|dc:subject|cp:keywords)>")
_TAG_RE = re.compile(r"<[^>]+>")

logger = logging.getLogger(__name__)


def is_searchable_attachment(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def extract_attachment_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if path.stat().st_size > MAX_ATTACHMENT_BYTES:
            return ""
        if suffix in PDF_EXTENSIONS:
            return _pdf_text(path)
        if suffix in OFFICE_PARTS:
            return _office_text(path, OFFICE_PARTS[suffix])
        if suffix in PLAIN_TEXT_EXTENSIONS:
            return _plain_text(path.read_bytes()[:MAX_TEXT_CHARS])
    except Exception:
        # A malformed attachment must not interrupt the analysis of the mailbox.
        logger.info("Texte de piece jointe illisible (%s)", suffix, exc_info=True)
    return ""


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path))
    if reader.is_encrypted and not reader.decrypt(""):
        return ""
    parts: list[str] = []
    length = 0
    for page in reader.pages[:MAX_PDF_PAGES]:
        text = page.extract_text() or ""
        parts.append(text)
        length += len(text)
        if length >= MAX_TEXT_CHARS:
            break
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def _office_text(path: Path, part_prefixes: tuple[str, ...]) -> str:
    with zipfile.ZipFile(path) as archive:
        members = [
            info for info in archive.infolist()
            if info.filename.endswith(".xml") and info.filename.startswith(part_prefixes)
        ]
        if sum(info.file_size for info in members) > MAX_UNCOMPRESSED_OFFICE_BYTES:
            return ""
        parts = [
            xml_to_text(archive.read(info).decode("utf-8", errors="replace"))
            for info in members
        ]
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def xml_to_text(xml: str) -> str:
    return html.unescape(_TAG_RE.sub("", _BLOCK_END_RE.sub("\n", xml)))


def _plain_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
