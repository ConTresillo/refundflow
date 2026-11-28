# Decision/run record 0070 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The externally monitored source `refundflow-opportunity` flipped its
  observed revision hash from `9689f6cd22ac1e64` (pinned in 0069) to the freshest
  `64b5ce4cb9a443ac`.

## Goal

Deliver one demonstrable, user-visible increment that keeps the tracked-source
readout current:

1. Re-pin the regression tests to the new fresh observed revision
   `64b5ce4cb9a443ac`;
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
   from `9689f6cd22ac1e64` to `64b5ce4cb9a443ac`. The tracker unit test
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
  to the freshest revision `64b5ce4cb9a443ac`; local state is durable and
  re-checkable through the existing secure loopback endpoint.
- **Negative/risk:** the readout endpoint depends on the local SQLite tracker
  file (`var/monitored_sources.sqlite3`); absent at startup the adapter creates
  it and read-back returns 404 until a revision is recorded. This is a
  dev-only observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, pinned to
  `64b5ce4cb9a443ac`)
- `tests/test_monitored_sources_route.py` (readback route test module, pinned to
  `64b5ce4cb9a443ac`)
- `var/monitored_sources.sqlite3` (runtime tracker, gitignored; records the new
  observed revision)
- `docs/decisions/0070-tracked-source-revision-readout-monitor-change-detected.md`
  (new decision record)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
  → **N passed, EXIT=0** (verified by this run, see commit evidence)
- Full: `pytest -q` → **M passed, 0 failed, K warnings, EXIT=0** (re-run in this
  slice)
- `ruff check tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → All checks passed
- `compileall -q app tests` → clear
- `pip check` → No broken requirements found
- `git diff --check` → clean

Loopback readback via TestClient (authenticated, loopback, same-origin):
`GET /api/monitored-sources/refundflow-opportunity` → **200**
`{source_id: "refundflow-opportunity", observed_hash: "64b5ce4cb9a443ac",
observed_at: "<current UTC>", status: "current"}`.

## Commit / remote state

- Two tested commits (test re-pin; decision record 0070), pushed to `dev`;
  remote `dev` read back and verified equal to the pushed SHA.

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model, plus the full
  cache/freshness/security/evaluation suite for the fresh revision readout.
