# Decision/run record 0061 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `4a95c0ea17daa08b` to `34522637af8c8f37`.

## Goal

Deliver one demonstrable, user-visible increment that makes the newest tracked
source revision observable and re-checkable locally:

1. Persist the new observed revision hash `34522637af8c8f37` into the durable,
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
   `34522637af8c8f37` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via
   `SQLiteMonitoredSources.record()`. Persistence is fail-closed (invalid
   source_id / empty hash rejected with `ValueError`).
2. **Re-pin the regression tests to the new fresh revision.** Updated
   `tests/test_monitored_sources.py` and `tests/test_monitored_sources_route.py`
   so the selector fixtures (`FRESH_OBSERVED`) point at `34522637af8c8f37`.
3. **Readback + fail-closed.** The existing read-only endpoint
   `GET /api/monitored-sources/{source_id}` returns 200 with the fresh revision
   when authenticated, loopback, same-origin, and returns 401/404/403
   fail-closed for unauthenticated/unknown/cross-origin callers.

## Consequences

- **Positive:** the pilot/operator now sees the external tracked source at its
  newest revision `34522637af8c8f37`; the local state is durable and re-checkable
  through the existing secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if absent at startup the tracker adapter
  creates it and read-back returns 404 until a revision is recorded. This is
  dev-only observability, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, re-pinned to the
  fresh observed hash `34522637af8c8f37`)
- `tests/test_monitored_sources_route.py` (readback route test module, re-pinned
  to the fresh observed hash `34522637af8c8f37`)

## Tests / evaluation (actual output)

Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q` →
**8 passed (4 + 4), EXIT=0**

Full: `pytest --tb=short -p no:cacheprovider -q` → **219 passed, 222 warnings in
16.68s, EXIT=0** (unchanged from the prior run; the re-pin changed no behaviour,
only the regression pin).

`ruff check app tests` → **All checks passed!** (6 pre-existing errors in
untracked `tests/test_corpus_mutations.py` left untouched per the preservation
rule)

`compileall -q app tests` → **clear**
`pip check` → **No broken requirements found.**
`git diff --check` → **clean**

## Commit / remote state

Two commits this run (test re-pin; decision record), pushed to `dev`. Remote
`dev` read back to verify the pushed SHA (see commit notes).

## Blocker / next step

No hard blocker. Next bounded follow-on: P7a audit/read-model and the full
evaluation/cache/security/evaluation suite for the fresh revision readout.
