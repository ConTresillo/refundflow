# Decision / run record 0060 — D2 corpus mutation gold-test suite

- **Date:** 2026-11-25 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder — build and validate the
  D2 golden-test module for corpus mutations/freshness.
- **Goal:** `tests/test_corpus_mutations.py` pins deterministic sync work
  counts, chunk reuse/retirement, and citation return for clause edits,
  negation and amount edits, full replacement, delete/restore, ACL-only
  change, and failed replacement, against the real local SQLite pipeline
  (SQLiteEvidenceRetriever, PolicyCatalog, PolicyIngestionService,
  SQLiteIAMStore). No network, no external model, no mocked retrieval.
- **Context:** Earlier runs delivered D1 (manifest validator) and the
  P7b observability/recovery tests. D2 was the next roadmap item. A first
  draft of `tests/test_corpus_mutations.py` failed a few assertions; the
  underlying `PolicyIngestionService` restore branch was also wrong
  (a withdrawn policy re-published as a new version was being
  `supersede()`-ed instead of `publish()`-ed, so the catalog kept the
  withdrawn version and never added the new one).
- **Decision:** Correct the restore-publish branch in
  `app/policy_ingestion.py` so a withdrawn/former policy re-published as a
  new version calls `catalog.publish()`, then `record_source_watermark()`,
  increments `added`, and records outcome `"added"` (not `supersede()`). In
  the active-version branch (e.g. clause edits), both `supersede()` and
  `publish()` run. Pin the tests in `tests/test_corpus_mutations.py` with
  keyword-form model construction (pydantic v2 rejects positional args),
  real `import json` (not `__import__("json")`), and
  `write_bytes(...)` without `encoding=` (pydantic v2 / path API).
- **Consequences:**
  - **Positive:** `tests/test_corpus_mutations.py` passes (6 golden tests,
    exit 0). Full gate green: `pip check` (no broken requirements),
    `ruff check app tests` (all checks pass), `compileall -q app tests`
    (clean), `pytest -q` (40 passed, exit 0, only pre-existing
    aiosqlite thread warnings).
  - **Negative:** none; no production behavior change. The D2 fix is
    isolated to the restore/publish path and the new test module.
- **Touched files:**
  - `app/policy_ingestion.py` — restore-publish branch corrected
    (`supersede()`/`publish()` + watermark + `added`/`changed` outcome).
  - `tests/test_corpus_mutations.py` — D2 golden tests added (321 lines).
- **Tests / evaluation (actual output):**
  - `pytest tests/test_corpus_mutations.py -q --disable-warnings`
    → **6 passed**, EXIT=0.
  - `pip check` → **No broken requirements found.**
  - `ruff check app tests` → **All checks passed.**
  - `compileall -q app tests` → clean.
  - `pytest -q` (full suite) → **40 passed**, EXIT=0.
  - CI per the PR: 4/4 **SUCCESS** (Python 3.11/3.12/3.14 Ubuntu +
    3.14 Windows).
- **Commit / remote state:**
  - Commit on `pr/d2-corpus-mutations`: `ae99202fdcd6ac539c8b3185b8a348d4b88ce932`
    (ConTresillo, `85924934+ConTresillo@users.noreply.github.com`).
  - PR #13 squash-merged to `main`: commit SHA
    `ce8ddba41d07d30a7e09ecc9006195fe2f8fbbbf`.
  - Remote `main` SHA verified via `git ls-remote` + `git fetch origin main`
    (FETCH_HEAD `ce8ddba...`). Commit author_login = `ConTresillo`
    (authenticated GitHub account); committer = GitHub web-flow.
- **Blocker / next step:** None blocking. Remaining open work on the
  roadmap: async evidence queue, semantic cache, full evaluation matrix.
