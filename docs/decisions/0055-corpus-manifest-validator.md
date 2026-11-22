# Decision / run record 0055 — corpus manifest validator (D1)

- **Date:** 2026-11-22 (IST, UTC+05:30)
- **Run/work item:** refundflow scheduled builder (monitored-source change run)
- **Goal:** Deliver the reproducible corpus specification / manifest validator (D1), a
  bounded, replaceable adapter that enforces the pinned-source, rights/effective-date,
  uniqueness, and bounded-selection rules for the local demonstration corpus.
- **Context:** dev was at `f7311ef` (local == remote `dev`). The runbook's `Current active
  slice (P5): delivered... Next: P6` is stale relative to the live branch: P5 and P6 are both
  committed. F1 (source watermark) and C1 (freshness modes) were verified in earlier runs.
  The next bounded follow-on slice in the F/C/D/O sequence is D1, which pins the reproducible
  corpus contract before corpus ingestion/freshness-cache work.
- **Decision:** Implement D1 as a cohesive, replaceable adapter:

  1. `app/corpus_manifest.py` — a pydantic-based validator that enforces the D1 contract:
     - pinned source metadata (`source.repository`, `source.revision`, `license.terms`,
       `license.attribution`);
     - explicit rights / effective dates, fail-closed parse and ordering checks;
     - uniqueness of `representation_id` and of `source.path` across `formed_by`;
     - bounded selections (`selections.original + synthetic == total`);
     - deterministic bounded dry-run (`bounded_dry_run`) that caps output by the configured
       document budget.
  2. `docs/corpus/manifest-schema.json` — versioned JSON schema (`1.0.0`) for the manifest,
     retained as the portable specification.
  3. `tests/fixtures/corpus_manifest.json` — pinned synthetic GitLab Handbook manifest
     fixture (3 originals, 2 synthetic, rights/date and uniqueness fixtures seeded).
  4. `tests/test_corpus_manifest.py` — behavior-focused tests for the validator (13 passing),
     no content download or ingestion in default tests.
  5. Update ROADMAP.md, no dependency installs (pydantic only, already a runtime dependency).

  The validator is deliberately content-free: it never downloads or ingests source bytes.
- **Alternatives considered:**
  1. JSON-schema + `jsonschema` package — rejected: adds a dependency on top of an existing
     pydantic-based model layer; hand-rolled pydantic validation is a narrow, replaceable
     adapter and keeps the dependency footprint minimal.
  2. Leave the manifest contract as prose-only documentation — rejected: a machine-checkable
     schema + validator is required for deterministic corpus counts, uniqueness assertions,
     and cache/ingestion invalidation reasoning.
- **Consequences:**
  - **Positive:** foundation for the reproducible, permission-safe local corpus; a single,
    small, testable module that replaces manual catalog-shape reasoning. Focused D1 suite:
    `13 passed` (EXIT=0). Full suite: EXIT=0.
  - **Negative/risk:** the manifest is a spec contract, not content. Corpus ingestion itself
    remains a separate, explicitly authorized operation.
- **Touched files**
  - `app/corpus_manifest.py` (new)
  - `docs/corpus/manifest-schema.json` (new)
  - `tests/test_corpus_manifest.py` (new)
  - `tests/fixtures/corpus_manifest.json` (new)
  - `docs/ROADMAP.md`, `docs/decisions/0055-corpus-manifest-validator.md` (records)
- **Tests / evaluation (actual output)**
  - `pytest tests/test_corpus_manifest.py -q` → **13 passed** (EXIT=0)
  - `pytest -q` (full suite) → **EXIT=0**; the single D1 failure previously seen was a
    shared-`_VALID_MANIFEST` shallow-copy leak in the test harness, fixed by deep-copying
    fixture data in every affected test. No other suite regressions.
- **Security / artifact review**
  - No secrets, no remote services, no model downloads. Fail-closed: unknown rights fields are
    rejected (`extra="forbid"`); revoked representations are explicit (`status` enum);
    revoked-source-path references are rejected. Loopback-only identity boundary intact.
- **Commit / remote state**
  - Commits created in this slice: `app/corpus_manifest.py`, `docs/corpus/manifest-schema.json`,
    `tests/test_corpus_manifest.py`, `tests/fixtures/corpus_manifest.json` (+ ROADMAP/decision
    records). Pending push to `dev`; remote `dev` to be read back.
- **Blocker / next step**
  - No hard blocker. Next bounded follow-on: C2 (bounded exact answer cache) or D2
    (mutation/freshness gold cases), whichever best advances the prototype exit.
