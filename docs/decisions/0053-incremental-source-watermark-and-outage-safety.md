# 0053 — incremental source watermark and outage safety

## Goal
Make incremental source reconciliation fail closed on outage and report lag on
partial failure: unchanged sources reparse nothing, a failed upload preserves the
previous evidence and reports lag, a deleted source directory withdraws nothing,
and an out-of-order or changed source cannot overwrite a newer active revision.

## Context
`PolicyIngestionService.sync()` reconciles a curated source manifest against the
SQLite catalog and lexical index atomically. Earlier P4 work covered the per-format
adapters; the incremental bookkeeping itself was incomplete. Known failure modes
that needed durable protection:

- A malformed or corrupt uploaded document must never advance or change the active
  version (evidence must be preserved).
- Deleting the source directory must raise failure and withdraw nothing.
- If the source content changes between the preview snapshot and a sync run, the
  stale/changed event must be rejected and the catalog left untouched.
- On a full successful reconciliation only does the source watermark (observed
  revision) advance; on failure the watermark must not move.

## Decision
- Added a `source_watermarks` table keyed by policy id, holding the observed
  content hash, observed/at last-success/last-failure timestamps, and a
  `failed_attempts` counter.
- `record_source_watermark` stores `observed_hash`, `observed_at`, and
  `last_success_at` (only advancing on a full, committed reconciliation).
- `record_source_failure` records `last_failure_at` and increments
  `failed_attempts` with an explicit `BEGIN IMMEDIATE`/`COMMIT` on a fresh
  connection so it persists on the same SQLite file the ingestion writes to.
- `sync()` advances the watermark only after the catalog and index commits
  succeed, and it is never advanced on rejection paths (future/effective, version
  immutability, directory outage, content-changed-without-version-change).
- Per-format upload failures (`replace_pdf` / `replace_docx` / `replace_text` /
  `replace_markdown`) record `record_file_status` (classifies the failure) plus
  `record_source_failure` (records lag), then propagate `DocumentParseError` so
  the operator still sees the failure.
- Directory outage: `load_registry()` raises `FileNotFoundError`; it is raised
  before any catalog transaction, so the active policy and index are never
  withdrawn.

## Consequences
- Positive: ingestion is now fail-closed on outage/partial failure; pending
  runs and failed uploads are visible in sync status without mutating durable
  evidence.
- Negative: `source_watermarks` is a new table; the `replace_*` failure path
  writes an extra failure row, which is acceptable for a local simulation.

## Verification
- `pytest tests/test_policy_ingestion.py -q` → 16 passed.
- Focused parser+ingestion suite → 22 passed.
- Full `pytest -q` → 115 passed, 13 warnings, 0 failed.
- `ruff check app tests` → All checks passed.
- `compileall -q app tests` → COMPILE_OK.
- `pip check` → 1 pre-existing, unrelated dev-environment constraint
  (browser-harness requires pillow==12.3.0; installed pillow is 13.0.0), unchanged
  by this slice and not a regression.

## Commit / remote
- Commit `af2c9c419274e12e63cbcf9017cb4b7ae1670cb9` pushed to `dev`;
  `origin/dev` read back equal to the local head.

## Next
- F1 completion enables the fresh/TTL/critical-mode (C1) slice and, later,
  P5's durable async evidence run.
