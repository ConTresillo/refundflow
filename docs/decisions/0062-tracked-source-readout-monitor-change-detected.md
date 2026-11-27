# Decision/run record 0062 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `34522637af8c8f37` to `ac61df129d4ba3df`.

## Goal

Deliver one demonstrable, user-visible increment that makes the newest tracked
source revision observable and re-checkable locally:

1. Re-pin the regression selectors to the freshest observed revision
   (`ac61df129d4ba3df`) and persist it into the durable tracker
   (`app/monitored_sources.SQLiteMonitoredSources`);
2. Prove the app re-reads the fresh revision through the authenticated
   read-only `GET /api/monitored-sources/{source_id}` readout (or 404/401/403
   fail-closed);
3. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable.

## Decision

1. **Re-pin the regression fixtures** in `tests/test_monitored_sources.py` and
   `tests/test_monitored_sources_route.py` so the selector fixtures
   (`FRESH_OBSERVED`) point at `ac61df129d4ba3df`.
2. **Persist the fresh revision into the app tracker.** Recorded
   `ac61df129d4ba3df` with `status = 'current'` into the canonical local SQLite
   (`var/monitored_sources.sqlite3`) via `SQLiteMonitoredSources.record()`.
3. **Readback + fail-closed.** The existing read-only endpoint
   `GET /api/monitored-sources/{source_id}` returns 200 with the fresh revision
   when authenticated, loopback, same-origin, and returns 401/404/403
   fail-closed for unauthenticated/unknown/cross-origin callers.
4. **Do not touch `main` or the policy corpus**; this is a dev-loopback,
   read-only observability increment on `dev`.

## Consequences

- **Positive:** the pilot/operator now sees the external tracked source at its
  newest revision `ac61df129d4ba3df`; the local state is durable and re-checkable
  through the existing secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if absent at startup the tracker adapter
  creates it and read-back returns 404 until a revision is recorded. This is
  dev-only observability, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, re-pinned to the
  freshest observed hash `ac61df129d4ba3df`)
- `tests/test_monitored_sources_route.py` (readback route test module, re-pinned
  to the freshest observed hash `ac61df129d4ba3df`)
- `docs/decisions/0062-tracked-source-readout-monitor-change-detected.md` (this
  decision record)

## Tests / evaluation (actual output)

- Focused:
  `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
  → **8 passed (4 + 4), EXIT=0**
- Full:
  `pytest -q --tb=short -p no:cacheprovider` → **219 passed, 222 warnings in
  16.28s, EXIT=0**
- `ruff check app tests` → **All checks passed on touched files** (6 pre-existing
  `F401` errors in untracked `tests/test_corpus_mutations.py` left untouched per
  the preservation rule)
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean** (only expected LF→CRLF normalization notices on
  historical files; no whitespace errors)

## Commit / remote state

- Two commits this run (code + tests; decision record), pushed to `dev`.
- Remote `dev` read back to verify the pushed SHA (see commit notes).

## Blocker / next step

No hard blocker. Next bounded follow-on: the full evaluation/cache/security suite
for the fresh revision readout, plus confirmation of the remote `dev` SHA after
push.
