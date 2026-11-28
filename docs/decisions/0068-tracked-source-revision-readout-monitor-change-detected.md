# Decision/run record 0068 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The externally monitored source `refundflow-opportunity` flipped its
  observed revision hash from `183babc2007d8985` (pinned in 0067) to the freshest
  `cbc20d9806ec65b6`.

## Goal

Deliver one demonstrable, user-visible increment that keeps the tracked-source
readout current:

1. Re-pin the regression tests to the new fresh observed revision
   `cbc20d9806ec65b6`;
2. Persist the new observed revision hash into the durable SQLite tracker
   (`app/monitored_sources.SQLiteMonitoredSources`);
3. Prove read-back through the authenticated, deny-by-default
   `GET /api/monitored-sources/{source_id}` readout; and
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this run makes its
new revision durable and observable without touching the policy corpus.

## Decision

1. **Re-pin regression fixtures.** Updated `FRESH_OBSERVED` in
   `tests/test_monitored_sources.py` and `tests/test_monitored_sources_route.py`
   from `183babc2007d8985` to `cbc20d9806ec65b6`. The tracker unit test
   (`records_observed_revision_and_advances_hash`) still proves an even later
   revision advances the stored hash; no other fixtures changed.
2. **Persist the new observed revision.** The read-back test seeds the durable
   SQLite tracker (`var/monitored_sources.sqlite3`) via
   `SQLiteMonitoredSources.record()` with the new hash and reads it back through
   the authenticated readout endpoint. Persistence is fail-closed (invalid
   source_id / empty hash rejected with `ValueError`).
3. **Read-back and deny-by-default.** Unknown source → 404; unauthenticated → 401;
   non-loopback → 403. Authenticated loopback read-back returns the fresh hash
   with `status = 'current'`.

## Consequences

- **Positive:** the pilot/operator sees that the external tracked source advanced
  to the freshest revision `cbc20d9806ec65b6`; local state is durable and
  re-checkable through the existing secure loopback endpoint.
- **Negative/risk:** the readout endpoint depends on the local SQLite tracker
  file (`var/monitored_sources.sqlite3`); absent at startup the adapter creates
  it and read-back returns 404 until a revision is recorded. This is a
  dev-only observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, pinned to
  `cbc20d9806ec65b6`)
- `tests/test_monitored_sources_route.py` (readback route test module, pinned to
  `cbc20d9806ec65b6`)
- `var/monitored_sources.sqlite3` (runtime tracker, gitignored; records the new
  observed revision)
- `docs/decisions/0068-tracked-source-revision-readout-monitor-change-detected.md`
  (new decision record)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
  → **8 passed, EXIT=0**
- Full: `pytest -q` → **219 passed, 0 failed, 222 warnings, EXIT=0**
- `ruff check tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → All checks passed
- `compileall -q app tests` → clear
- `pip check` → No broken requirements found
- `git diff --check` → clean
- Loopback readback via TestClient (authenticated, loopback, same-origin):
  `GET /api/monitored-sources/refundflow-opportunity` → **200**
  `{source_id: "refundflow-opportunity", observed_hash: "cbc20d9806ec65b6",
  observed_at: "2026-11-27T00:00:00+00:00", status: "current"}` and 404 for an
  unknown source.

## Commit / remote state

- One tested commit (regression fixture re-pin; decision record 0068), pushed to
  `dev`; remote `dev` read back and verified equal to the pushed SHA.

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model, plus the full
  cache/freshness/security/evaluation suite for the fresh revision readout.
