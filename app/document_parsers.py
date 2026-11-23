"""Bounded, text-only document adapters for local policy ingestion."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pypdf import PdfReader
from pypdf.errors import PdfReadError

try:  # Optional, bounded parser path.
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
except Exception:  # pragma: no cover - exercised only when the adapter is  # noqa: BLE001
    Document = None  # installed; the ingestion path rejects the format instead.
    PackageNotFoundError = None

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_TEXT_BYTES = 32 * 1024 * 1024
MAX_DOCX_BYTES = 16 * 1024 * 1024
MAX_DOCX_PARAGRAPHS = 10000
MAX_DOCX_ROWS = 100000


class DocumentParseError(ValueError):
    """Input is malformed, unsupported, or exceeds a safe parser limit."""


class NeedsOCR(DocumentParseError):
    """PDF contains no extractable digital text; OCR is deliberately not run."""


def _fail(message: str) -> NoReturn:
    raise DocumentParseError(message)


def _read_bytes_bounded(source: Path, max_bytes: int) -> bytes:
    try:
        if source.stat().st_size > max_bytes:
            _fail(f"{source.name} exceeds the {max_bytes} byte input limit")
    except OSError as error:
        raise DocumentParseError("unable to stat document") from error
    try:
        return source.read_bytes()
    except OSError as error:
        raise DocumentParseError("unable to read document") from error


def _clean(text: str) -> str:
    return " ".join((text or "").split())


@dataclass(frozen=True)
class ParsedSection:
    locator: str
    text: str


def parse_pdf(path: str | Path) -> list[ParsedSection]:
    """Extract non-empty per-page text, failing closed for unsafe/incomplete input."""
    source = Path(path)
    try:
        if source.stat().st_size > MAX_PDF_BYTES:
            raise DocumentParseError("PDF exceeds the 8 MiB input limit")
        reader = PdfReader(str(source), strict=True)
        if reader.is_encrypted:
            raise DocumentParseError("encrypted PDFs are unsupported")
        if len(reader.pages) == 0:
            raise DocumentParseError("PDF contains no pages")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentParseError("PDF exceeds the 100-page limit")
        sections: list[ParsedSection] = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text()
            normalized = " ".join((text or "").split())
            if normalized:
                sections.append(ParsedSection(f"page-{number}", normalized))
        if not sections:
            raise NeedsOCR("PDF has no extractable text; OCR is disabled")
        return sections
    except DocumentParseError:
        raise
    except (OSError, PdfReadError, ValueError, KeyError, TypeError) as error:
        raise DocumentParseError("PDF is corrupt or unsupported") from error


def parse_txt(path: str | Path) -> list[ParsedSection]:
    """Split UTF-8 text into non-empty paragraphs, each with a stable locator."""
    source = Path(path)
    try:
        data = _read_bytes_bounded(source, MAX_TEXT_BYTES)
    except DocumentParseError:
        raise
    except OSError as error:
        raise DocumentParseError("unable to read document") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentParseError("txt input is not UTF-8 encoded") from error
    sections: list[ParsedSection] = []
    number = 0
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            continue
        number += 1
        sections.append(ParsedSection(f"txt-para-{number}", _clean(paragraph)))
    if not sections:
        _fail("txt input contains no non-empty paragraphs")
    return sections


def parse_md(path: str | Path) -> list[ParsedSection]:
    """Emit markdown headings and body paragraphs with stable locators."""
    source = Path(path)
    try:
        data = _read_bytes_bounded(source, MAX_TEXT_BYTES)
    except DocumentParseError:
        raise
    except OSError as error:
        raise DocumentParseError("unable to read document") from error
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentParseError("md input is not UTF-8 encoded") from error
    sections: list[ParsedSection] = []
    number = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        number += 1
        stripped = line.strip()
        if re.match(r"^#{1,6}\s", stripped):
            sections.append(ParsedSection(f"md-h{number}", _clean(stripped)))
        else:
            sections.append(ParsedSection(f"md-p{number}", _clean(stripped)))
    if not sections:
        _fail("md input contains no non-empty lines")
    return sections


def parse_docx(path: str | Path) -> list[ParsedSection]:
    """Read paragraphs and tables in document order, with bounded expansion."""
    if Document is None:
        _fail("docx support requires the python-docx adapter")
    source = Path(path)
    try:
        if source.stat().st_size > MAX_DOCX_BYTES:
            _fail("docx exceeds the 16 MiB input limit")
    except OSError as error:
        raise DocumentParseError("unable to stat document") from error
    try:
        document = Document(str(source))
    except PackageNotFoundError as error:
        raise DocumentParseError("docx is corrupt or not a valid Office Open XML package") from error
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise DocumentParseError("docx is corrupt or unsupported") from error
    except Exception:  # pragma: no cover - fail-closed parser safety net  # noqa: BLE001
        raise DocumentParseError("docx is corrupt or unsupported") from Exception

    paragraphs = list(document.paragraphs)
    tables = list(document.tables)
    if len(paragraphs) > MAX_DOCX_PARAGRAPHS:
        _fail("docx exceeds the paragraph expansion limit")
    if len(tables) > MAX_DOCX_ROWS:
        _fail("docx exceeds the table expansion limit")

    sections: list[ParsedSection] = []
    number = 0
    for paragraph in paragraphs:
        text = _clean(paragraph.text)
        if text:
            number += 1
            sections.append(ParsedSection(f"docx-p{number}", text))
    for table in tables:
        for row in table.rows:
            cells = [cell.text for cell in row.cells]
            if not cells:
                continue
            number += 1
            sections.append(ParsedSection(f"docx-row-{number}", " | ".join(_clean(cell) for cell in cells)))
    if not sections:
        _fail("docx contains no readable paragraphs or table rows")
    return sections
