from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from mailflow.core import attachment_text
from mailflow.core.attachment_text import (
    extract_attachment_text,
    is_searchable_attachment,
    xml_to_text,
)
from mailflow.core.project_references import find_project_numbers


def write_zip(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return path


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with a Helvetica text line, built with exact xref offsets."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(output)


def test_word_number_split_across_formatting_runs_stays_whole(tmp_path: Path) -> None:
    document = (
        '<w:document><w:body><w:p><w:r><w:t>Offre 2025-</w:t></w:r>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>4893</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>Balcons &amp; garde-corps</w:t></w:r></w:p></w:body></w:document>"
    )
    path = write_zip(tmp_path / "offre.docx", {"word/document.xml": document})

    text = extract_attachment_text(path)

    assert find_project_numbers(text) == ["2025-4893"]
    assert "Balcons & garde-corps" in text


def test_excel_cells_do_not_glue_neighbour_values(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "liste.xlsx", {
        "xl/sharedStrings.xml": "<sst><si><t>Projet 2025-5012</t></si><si><t>2025</t></si></sst>",
        "xl/worksheets/sheet1.xml": (
            '<worksheet><sheetData><row><c r="A1"><v>2025</v></c>'
            '<c r="B1"><v>4893</v></c></row></sheetData></worksheet>'
        ),
    })

    assert find_project_numbers(extract_attachment_text(path)) == ["2025-5012"]


def test_pdf_text_is_read(tmp_path: Path) -> None:
    path = tmp_path / "facture.pdf"
    path.write_bytes(minimal_pdf("Votre reference 2025-4893"))

    assert find_project_numbers(extract_attachment_text(path)) == ["2025-4893"]


def test_windows_encoded_text_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_bytes("Référence chantier 2026-0012".encode("cp1252"))

    assert "Référence chantier 2026-0012" in extract_attachment_text(path)


def test_damaged_or_unsupported_files_give_no_text(tmp_path: Path) -> None:
    broken = tmp_path / "casse.docx"
    broken.write_bytes(b"not a zip archive")
    broken_pdf = tmp_path / "casse.pdf"
    broken_pdf.write_bytes(b"%PDF-1.4 truncated")
    image = tmp_path / "plan.dwg"
    image.write_bytes(b"2025-4893")

    assert extract_attachment_text(broken) == ""
    assert extract_attachment_text(broken_pdf) == ""
    assert extract_attachment_text(image) == ""


def test_oversized_files_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attachment_text, "MAX_ATTACHMENT_BYTES", 10)
    path = tmp_path / "gros.txt"
    path.write_text("Projet 2025-4893", encoding="utf-8")

    assert extract_attachment_text(path) == ""


def test_zip_declaring_huge_contents_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attachment_text, "MAX_UNCOMPRESSED_OFFICE_BYTES", 20)
    path = write_zip(tmp_path / "bombe.docx", {
        "word/document.xml": "<w:t>2025-4893</w:t>" + " " * 100,
    })

    assert extract_attachment_text(path) == ""


def test_searchable_attachment_names() -> None:
    assert is_searchable_attachment("Offre 2025-4893.PDF")
    assert is_searchable_attachment("metre.xlsx")
    assert not is_searchable_attachment("photo.jpg")
    assert not is_searchable_attachment("plan.dwg")


def test_xml_entities_are_decoded() -> None:
    assert xml_to_text("<a:p><a:t>A &lt; B</a:t></a:p>") == "A < B\n"
