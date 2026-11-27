# Decision/run record 0063 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `ac61df129d4ba3df` to `18682f53fb8e1d7e`.

## Goal

Deliver one demonstrable, user-visible increment that makes the newest tracked
source revision observable and re-checkable locally:

1. Re-pin the regression fixtures to the freshest observed revision
   (`18682f53fb8e1d7e`) and persist it into the durable tracker
   (`app/monitored_sources.SQLiteMonitoredSources`);
2. Prove the app re-reads the fresh revision through the authenticated
   read-only `GET /api/monitored-sources/{source_id}` readout (or 404/401/403
   fail-closed);
3. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable.

## Decision

1. **Re-pin the regression tests.** Updated `tests/test_monitored_sources.py`
   and `tests/test_monitored_sources_route.py` so the selector fixtures
   (`FRESH_OBSERVED`) point at `18682f53fb8e1d7e`.
2. **Persist into the existing app SQLite.** Recorded
   `18682f53fb8e1d7e` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via
   `SQLiteMonitoredSources.record()`. Persistence is fail-closed (invalid
   source_id / empty hash rejected with `ValueError`).
3. **Readback + fail-closed.** The existing read-only endpoint
   `GET /api/monitored-sources/{source_id}` returns 200 with the fresh revision
   when authenticated, loopback, same-origin, and returns 401/404/403
   fail-closed for unauthenticated/unknown/cross-origin callers.

## Consequences

- **Positive:** the pilot/operator now sees the external tracked source at its
  newest revision `18682f53fb8e1d7e`; the local state is durable and re-checkable
  through the existing secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if absent at startup the tracker adapter
  creates it and read-back returns 404 until a revision is recorded. This is
  dev-only observability, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, re-pinned to the
  freshest observed hash `18682f53fb8e1d7e`)
- `tests/test_monitored_sources_route.py` (readback route test module, re-pinned
  to the freshest observed hash `18682f53fb8e1d7e`)

## Tests / evaluation (actual output)

- Focused:
  `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
  → **8 passed (4 + 4), EXIT=0**
- Full:
  `pytest --tb=short -p no:cacheprovider -q` → **219 tests collected, all
  passed (EXIT=0); 13 pre-existing LangSmith deprecation warnings carried
  forward** (unchanged by this re-pin, which alters no behaviour)
- `ruff check app tests` → **All clear on touched files.** The 6 reported errors
  (`F401` x2, `RUF059` x2, `F841`, `B017`) are all pre-existing in the
  *untracked* `tests/test_corpus_mutations.py` and untouched per the
  preservation rule.
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean**

## Commit / remote state

- Commit `6bcabf4d05c67e8768969d19f77575e19149bd6b` was the prior remote HEAD
  for `dev` (= `origin/dev`).
- This run produces two commits (re-pin tests; decision record), pushed to `dev`.
- Remote `dev` read back to verify the pushed SHA (see commit notes).

## Blocker / next step

No hard blocker. Next bounded follow-on: P7a audit/read-model and the
fully-evaluated tracked-source freshness readout suite.
