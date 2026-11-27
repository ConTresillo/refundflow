# Decision/run record 0060 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-27 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `fc9fc674b8a7439e` to `4a95c0ea17daa08b`.

## Goal

Deliver one demonstrable, user-visible increment that makes the newest tracked
source revision observable and re-checkable locally:

1. Persist the new observed revision hash `4a95c0ea17daa08b` into the durable,
   replaceable SQLite tracker (`app/monitored_sources.SQLiteMonitoredSources`);
2. Re-expose the read-only `GET /api/monitored-sources/{source_id}` readout,
   now returning the freshest revision;
3. Re-pin the regression tests to the new fresh revision and prove readback +
   fail-closed behavior;
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable.

## Decision

1. **Persist into the existing app SQLite.** Recorded the fresh revision
   `4a95c0ea17daa08b` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via `SQLiteMonitoredSources.record()`.
   Persistence is fail-closed (invalid source_id / empty hash rejected with
   `ValueError`).
2. **Re-pin the regression tests to the new fresh revision.** Updated
   `tests/test_monitored_sources.py` and `tests/test_monitored_sources_route.py`
   so the selector fixtures (`FRESH_OBSERVED`) point at `4a95c0ea17daa08b`.
3. **Readback + fail-closed.** The existing read-only endpoint
   `GET /api/monitored-sources/{source_id}` still returns 200 with the fresh
   revision when authenticated, loopback, same-origin, and 404/401/403 fail-closed.
4. **Do not touch `main` or the policy corpus**; this is a dev-loopback,
   read-only observability increment on `dev`.

## Consequences

- **Positive:** the pilot/operator now sees the external tracked source at its
  newest revision `4a95c0ea17daa08b`; the local state is durable and re-checkable
  through the existing, secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the new endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if that file is absent at app startup the
  tracker adapter creates it and the read-back returns 404 until a revision is
  recorded. This is a dev-only observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, re-pinned to the
  fresh observed hash `4a95c0ea17daa08b`)
- `tests/test_monitored_sources_route.py` (readback route test module, re-pinned
  to the fresh observed hash `4a95c0ea17daa08b`)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **8 passed (4 + 4), EXIT=0**
- Full: `pytest --tb=short -p no:cacheprovider` → **219 passed, 222 warnings in
  16.68s, EXIT=0**
- `ruff check app tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **All checks passed!** (6 pre-existing errors in untracked
  `tests/test_corpus_mutations.py` left untouched per preservation rule)
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean** (only expected LF→CRLF normalization notices on
  historical files; no whitespace errors)

## Commit / remote state

- Two commits this run (test regressions; decision record), pushed to `dev`,
  remote `dev` read back to verify the pushed SHA (see commit notes).
- Pre-existing untracked files (`.preserve/`, `docs/architecture/rag-production-research.md`,
  `docs/decisions/0033-principal-review-execution-contract.md`,
  `tests/test_corpus_mutations.py`) preserved and intentionally not committed.

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model and the full
  evaluation/cache/security/evaluation suite for the fresh revision readout,
  plus confirmation of the remote `dev` SHA after push.
