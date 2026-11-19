from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.document_parsers import DocumentParseError, NeedsOCR, parse_pdf


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
