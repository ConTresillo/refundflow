"""Bounded, text-only document adapters for local policy ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 100


class DocumentParseError(ValueError):
    """Input is malformed, unsupported, or exceeds a safe parser limit."""


class NeedsOCR(DocumentParseError):
    """PDF contains no extractable digital text; OCR is deliberately not run."""


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
