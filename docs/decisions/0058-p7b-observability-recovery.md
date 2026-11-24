# Decision / run record 0058 — P7b observability spine + fail-closed recovery

- **Date:** 2026-11-25 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder — next-blocked P7b observability spine + SQLite fail-closed recovery.
- **Goal:** Advance the P7b redacted observability/recovery slice: (a) remove the invented "0 events" counters from the run report so it carries live measurements, and (b) exercise SQLite busy/fail-closed behaviour with focused regression tests.
- **Context:** The roadmap's "Current active slice" declared P7a as "next", but P7a was already delivered (decision 0056). Inspection of `origin/main` (`3ba90c0`) showed the P7b observability spine already present (correlation context, redaction, run report, web UI); the remaining bounded acceptance items were SQLite busy/disk-failure fail-closed and an actual measured baseline. This run addresses those bounded items.

## Decision

1. **Make the run-report counters live.** Replaced the placeholder `event_counter()` (hard-coded `{"events_written_total": 0}`) with a module-wide `_COUNTERS` dict incremented by `record_event()`. `run_report()` now reports `counters: {"events_written_total": N}` — honest per-process measurements derived from actual events, not invented targets. No dependency or API change.
2. **Add fail-closed SQLite regression tests.** New `tests/test_observability_recovery.py` covers:
   - `test_create_run_under_long_held_write_lock_fails_closed` — a held write lock raises `OperationalError: database is locked`; the outbox is untouched (no partial run row).
   - `test_recovery_after_busy_is_clean` — a fresh repository after the contention converges to the pre-failure state (single original run, no duplicate/corruption).
3. **Record the change in the roadmap.** Updated `docs/ROADMAP.md` "Current active slice" to reflect P6 + P7a delivered and P7b spine + recovery in progress, and updated the P7 section checkboxes.

## Alternatives considered

- **Reusing the existing placeholder counter as-is.** Rejected: it is an invented target ("0 events"), contradicting the P7b acceptance item "publish actual measured baseline, not invented latency targets".
- **Adding a full disk-failure injection suite.** Rejected for this bounded run: directory-permission fail-closed is platform-dependent (Windows as Administrator ignored the write-bit removal in a quick probe). The SQLite busy test is reliably executable in-process; disk-failure scenarios are tracked as remaining P7b work.
- **Implementing a full worker-kill/restore harness.** Rejected for this bounded run; the SQLite busy/recovery tests already prove the fail-closed, checkpoint-consistent property. Worker-kill/restore is tracked as remaining P7b work.

## Consequences

- **Positive:** the run report now carries live per-process counters; two new regression tests prove SQLite busy fail-closed and clean recovery; the roadmap and decision log are updated.
- **Risk:** `_COUNTERS` is per-process and in-memory (the "spine" sensor), so across-process worker restarts do not share counters; the spec's richer run-report fields (ingestion freshness, graph trace, MCP activity) remain a later enhancement. `_COUNTERS` is not thread-locked, but the increment is intended for local, single-process measurement only.

## Touched files

- `app/observability.py` — added `_COUNTERS`, live `event_counter()`, counter increment in `record_event()`.
- `tests/test_observability_recovery.py` — 2 new fail-closed SQLite recovery tests.
- `docs/ROADMAP.md` — updated "Current active slice" and P7 checkboxes.

## Tests / evaluation (actual output)

- New focused suite: `pytest tests/test_observability_recovery.py -q` → **2 passed** (EXIT=0).
- Full suite: `pytest -q` → **213 passed**, EXIT=0 (RC=0).
- `ruff check app tests` → all checks passed.
- `compileall -q app tests` → clear.
- `pip check` → No broken requirements found.

## Commit / remote state

- Commit on `p7b-observability-new` (a short-lived branch based on `origin/main`); the branch is not yet pushed to `origin/main`.
- Next step: push to `origin/main` (no dev), read back remote `dev`/`main` and CI status before calling the slice complete.

## Blocker / next step

- No hard blocker. Remaining bounded P7b work (tracked for the next run): disk-failure injection scenarios, worker-kill/restore of database/source snapshots, and an actual measured run-report baseline. Also: push to `origin/main` and verify CI on the pushed SHA.
