# Decision/run record 0073 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The externally monitored source `refundflow-opportunity` advanced again. The locally pinned fresh observed revision `527017c0e4e8faaa` (decision 0072) was superseded by the freshest `92ae4ae4c0511114`.

## Goal

Deliver one demonstrable, user-visible increment that keeps the tracked-source
readout current, which requires:

1. Re-pin the regression tests to the new fresh observed revision
   `92ae4ae4c0511114`;
2. Persist the new observed revision hash into the durable SQLite tracker
   (`app/monitored_sources.SQLiteMonitoredSources`);
3. Prove read-back through the authenticated, deny-by-default
   `GET /api/monitored-sources/{source_id}` readout; and
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this run makes its
new revision durable and observable without touching the policy corpus.

Supersedes decision 0072.

## Decision

1. **Re-pin regression fixtures.** Updated `FRESH_OBSERVED` in
   `tests/test_monitored_sources.py` and `tests/test_monitored_sources_route.py`
   from `527017c0e4e8faaa` to `92ae4ae4c0511114`. The tracker unit test
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
  to the freshest revision `92ae4ae4c0511114`; local state is durable and
  re-checkable through the existing secure loopback endpoint.
- **Negative/risk:** the readout endpoint depends on the local SQLite tracker
  file (`var/monitored_sources.sqlite3`); absent at startup the adapter creates
  it and read-back returns 404 until a revision is recorded. This is a dev-only
  observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, pinned to
  `92ae4ae4c0511114`)
- `tests/test_monitored_sources_route.py` (read-back route test module, pinned to
  `92ae4ae4c0511114`)
- `docs/ROADMAP.md` (current-active-slice summary points at the new revision)
- `docs/decisions/0073-tracked-source-revision-readout-monitor-change-detected.md`
  (new decision record, supersedes 0072)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
  → **8 passed, 0 failed (EXIT=0)**
- Full: `pytest -q` → **EXIT=0, all suites green (no FAILED/ERROR)**
- `ruff check tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → All checks passed (EXIT=0)
- `compileall -q app tests` → clean (EXIT=0)
- `pip check` → No broken requirements found (EXIT=0)
- `git diff --check` → clean (EXIT=0)

Loopback readback via TestClient (authenticated, loopback, same-origin):
`GET /api/monitored-sources/refundflow-opportunity` → **200**
`{source_id: "refundflow-opportunity", observed_hash: "92ae4ae4c0511114",
observed_at: "2026-11-27T00:00:00+00:00", status: "current"}`.

Also confirmed, after the focused run, the durable tracker holds
`observed_hash = 92ae4ae4c0511114` with `status = current`.

## Commit / remote state

One tested commit (regression re-pin + roadmap refresh + this decision record),
pushed to `dev`; remote `dev` read back and verified equal to the pushed SHA.

## Blocker / next step

No hard blocker. Next bounded follow-on: P7a immutable evidence/operations
(append-only DB triggers, audit/read-model, redacted timeline UI).
