# Decision 0052 — incremental DOCX/TXT/MD ingestion for the policy RAG

- Date: 2026-11-22. Goal: extend the bounded document ingestion path beyond PDF to
  UTF-8 text, Markdown, and DOCX, with fail-closed parsing and quoted citations.
- Context: The policy catalog supports immutable versions and one SQLite transaction
  for catalog + lexical index updates. PDF replacement existed locally (P4a), but the
  corpus had no non-PDF source path, and the loopback upload endpoint only accepted
  PDF. The owner-directed prototype exit requires the same authorized-upload →
  cited-retrieval flow to work for the other supported formats.
- Decision: Add deterministic, bounded parsers for `.txt` (paragraphs), `.md`
  (headings `md-h` + paragraphs `md-p`), and `.docx` (paragraphs `docx-p` + table
  rows `docx-row`, in document order). Replace the existing `replace_pdf` method with
  a shared `_publish_new_version` transaction helper and one format-specific entry
  point per suffix, dispatched by `replace_document`. All parsers bound by byte/page/
  expansion limits, fail closed for corrupt/oversize/OCR-only input, and never mutate
  the active catalog on failure. Each publish supersedes the previous active version,
  re-indexes only the affected record, records per-document status, and preserves
  prior evidence on failure.
- Test-first: parser unit tests (stable locators, ordered paragraph/table output,
  corrupt-ZIP, non-ZIP, text/PDF oversize bounds) initially failed before the
  parser was implemented; they now pass. An ingestion test proves TXT → Markdown → DOCX
  replacement supersedes and cites new sections, and an unsupported-format upload is
  rejected.
- Touched: `app/document_parsers.py`, `app/policy_ingestion.py`,
  `tests/test_document_parsers.py`, `tests/test_policy_ingestion.py`,
  `pyproject.toml`, `requirements-dev.lock`, `docs/decisions/0052-…md`, `docs/ROADMAP.md`.
- Verification after final code edit: focused `.venv/Scripts/python.exe -m pytest
  tests/test_document_parsers.py tests/test_policy_ingestion.py -q` → 18 passed;
  `.venv/Scripts/python.exe -m pytest -q` → 115 passed; `ruff check app tests` all
  passed; `compileall` passed; `pip check` no broken requirements; `git diff --check`
  clean.
- Security/artifact review: no external service, credential, or model download. The
  DOCX adapter is a stdlib+ZIP/XML reader wrapped by python-docx; parser results are
  plain text excerpts only, with no macro/resource/URL fetching.
- Commit/remote/CI: pending.
- Next: wire the same formats into a small UI upload control and re-verify an
  authenticated loopback upload → cited retrieval end-to-end for a non-PDF format.
