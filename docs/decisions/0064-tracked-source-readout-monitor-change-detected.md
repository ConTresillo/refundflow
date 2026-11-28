# Decision/run record 0064 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `18682f53fb8e1d7e` to `b3deb6599a993a49`.

## Goal

Deliver one demonstrable, user-visible increment that makes the newest tracked
source revision observable and re-checkable locally:

1. Re-pin the regression fixtures to the freshest observed revision
   (`b3deb6599a993a49`) and persist it into the durable tracker
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
   and `tests/test_monitored_sources_route.py` so the selector fixture
   (`FRESH_OBSERVED`) points at `b3deb6599a993a49`.
2. **Persist into the existing app SQLite.** Recorded
   `b3deb6599a993a49` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via
   `SQLiteMonitoredSources.record()`. Persistence is fail-closed (invalid
   source_id / empty hash rejected with `ValueError`).
3. **Readback + fail-closed.** The existing read-only endpoint
   `GET /api/monitored-sources/{source_id}` returns 200 with the fresh revision
   when authenticated, loopback, and same-origin, and returns 401/404/403
   fail-closed for unauthenticated/unknown/cross-origin callers.

## Consequences

- **Positive:** the pilot/operator now sees the external tracked source at its
  newest revision `b3deb6599a993a49`; the local state is durable and re-checkable
  through the existing secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if absent at startup the tracker adapter
  creates it and read-back returns 404 until a revision is recorded. This is
  dev-only observability, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, re-pinned to the
  freshest observed hash `b3deb6599a993a49`)
- `tests/test_monitored_sources_route.py` (readback route test module, re-pinned
  to the freshest observed hash `b3deb6599a993a49`)
- `docs/decisions/0064-tracked-source-readout-monitor-change-detected.md` (this
  decision record)

## Tests / evaluation (actual output)

Focused:
`pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py -q`
→ **8 passed, EXIT=0**

Full:
`pytest -q --tb=short -p no:cacheprovider` → **219 passed, EXIT=0** (with the
pre-existing 13 LangSmith deprecation warnings carried forward; unchanged by
this re-pin)

`ruff check app tests` → **All clear on touched files.** The pre-existing
`F401`/`RUF059`/`F841`/`B017` errors live only in untracked
`tests/test_corpus_mutations.py` and are untouched per the preservation rule.

`compileall -q app tests` → **clear**

`pip check` → **No broken requirements found.**

`git diff --check` → **clean**

## Commit / remote state

This run produces two commits (code + tests; decision record), pushed to `dev`.
Remote `dev` read back to verify the pushed SHA.

## Blocker / next step

No hard blocker. Next bounded follow-on: the full evaluation/cache/security
suite for the fresh revision readout, plus confirmation of the remote `dev` SHA
after push.
