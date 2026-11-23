from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.document_parsers import (
    DocumentParseError,
    NeedsOCR,
    parse_docx,
    parse_md,
    parse_pdf,
    parse_txt,
)


def make_pdf(path: Path, text: str = "") -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    if text:
        from pypdf.generic import DecodedStreamObject

        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 20 200 Td (" + text.encode("ascii") + b") Tj ET"
        )
        from pypdf.generic import DictionaryObject, NameObject

        page[NameObject("/Contents")] = writer._add_object(stream)
        font = DictionaryObject()
        font[NameObject("/Type")] = NameObject("/Font")
        font[NameObject("/Subtype")] = NameObject("/Type1")
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = writer._add_object(font)
        resources = DictionaryObject()
        resources[NameObject("/Font")] = fonts
        page[NameObject("/Resources")] = resources
    with path.open("wb") as target:
        writer.write(target)


def test_extracts_digital_text_with_page_provenance(tmp_path):
    path = tmp_path / "policy.pdf"
    make_pdf(path, "Refund decisions require evidence")

    assert parse_pdf(path)[0].locator == "page-1"
    assert "Refund decisions require evidence" in parse_pdf(path)[0].text


def test_scanned_empty_and_encrypted_pdfs_fail_closed(tmp_path):
    empty = tmp_path / "scan.pdf"
    make_pdf(empty)
    with pytest.raises(NeedsOCR):
        parse_pdf(empty)

    encrypted = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("secret")
    with encrypted.open("wb") as target:
        writer.write(target)
    with pytest.raises(DocumentParseError, match="encrypted"):
        parse_pdf(encrypted)


def test_corrupt_and_oversize_inputs_fail_closed(tmp_path, monkeypatch):
    corrupt = tmp_path / "broken.pdf"
    corrupt.write_bytes(b"not a pdf")
    with pytest.raises(DocumentParseError):
        parse_pdf(corrupt)

    oversized = tmp_path / "large.pdf"
    oversized.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("app.document_parsers.MAX_PDF_BYTES", 4)
    with pytest.raises(DocumentParseError, match="8 MiB"):
        parse_pdf(oversized)


def write_docx(path: Path, paragraphs: list[str], table_rows: list[list[str]]) -> None:
    from io import BytesIO

    from docx import Document

    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    for row in table_rows:
        cells = len(row)
        table = document.add_table(rows=1, cols=cells)
        for index, cell_text in enumerate(row):
            table.rows[0].cells[index].text = cell_text
    buffer = BytesIO()
    document.save(buffer)
    path.write_bytes(buffer.getvalue())


def test_parse_txt_and_markdown_use_stable_locators(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("First paragraph.\n\nSecond paragraph: refund policy.", encoding="utf-8")
    assert [item.locator for item in parse_txt(txt)] == ["txt-para-1", "txt-para-2"]

    md = tmp_path / "notes.md"
    md.write_text("# Refund policy\n\nBody line.\n", encoding="utf-8")
    assert [item.locator for item in parse_md(md)] == ["md-h1", "md-p2"]


def test_parse_docx_preserves_paragraph_and_table_order(tmp_path):
    path = tmp_path / "policy.docx"
    write_docx(
        path,
        paragraphs=["Intro clause.", "Secondary clause."],
        table_rows=[["Damaged", "can be replaced"]],
    )
    sections = parse_docx(path)
    assert [item.locator for item in sections] == ["docx-p1", "docx-p2", "docx-row-3"]
    assert sections[0].text == "Intro clause."
    assert sections[2].text == "Damaged | can be replaced"


def test_txt_markdown_and_docx_fail_closed_on_corrupt_or_oversize(tmp_path, monkeypatch):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(DocumentParseError):
        parse_docx(bad)

    renamed = tmp_path / "not-docx.docx"
    renamed.write_bytes(b"PK-fake")
    with pytest.raises(DocumentParseError):
        parse_docx(renamed)

    monkeypatch.setattr("app.document_parsers.MAX_TEXT_BYTES", 4)
    small = tmp_path / "tiny.txt"
    small.write_bytes(b"12345")
    with pytest.raises(DocumentParseError, match="byte input limit"):
        parse_txt(small)
    monkeypatch.setattr("app.document_parsers.MAX_DOCX_BYTES", 4)
    with pytest.raises(DocumentParseError, match="16 MiB"):
        parse_docx(small)

    oversized = tmp_path / "large.pdf"
    oversized.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("app.document_parsers.MAX_PDF_BYTES", 4)
    with pytest.raises(DocumentParseError, match="8 MiB"):
        parse_pdf(oversized)
