# Decision/run record 0066 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-28 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `0f6332063c297896` to `513f7a2d124215e7`.

## Goal

Deliver one demonstrable, user-visible increment that makes the monitoring
tracked source's revision freshness visible and re-checkable locally:

1. Persist the new observed revision hash (`513f7a2d124215e7`) into the durable,
   replaceable SQLite tracker (`app/monitored_sources.SQLiteMonitoredSources`);
2. Expose the authenticated, read-only `GET /api/monitored-sources/{source_id}`
   readout, which returns the durable observed revision (or 404/401/403
   deny-by-default for unknown source, unauthenticated, non-loopback);
3. Pin the focused regression tests to the new fresh revision and prove readback;
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable without touching the policy corpus.

## Decision

1. **Persist into the existing app SQLite.** Recorded the fresh revision
   `513f7a2d124215e7` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via `SQLiteMonitoredSources.record()`.
   Persistence is fail-closed (invalid source_id / empty hash rejected with
   `ValueError`).
2. **Expose a read-only readout** wired into `create_app()` (already present),
   sharing the app's existing dev-loopback boundary, origin/CSRF enforcement,
   and deny-by-default posture. The runtime tracker now holds the new observed
   hash; unknown source → 404; unauthenticated → 401; non-loopback → 403.
3. **Test-first regression coverage.** Pinned both the route readback test and
   the tracker unit test to the new fresh revision hash `513f7a2d124215e7`.

## Consequences

- **Positive:** the pilot/operator can now see that the external tracked source
  advanced to the freshest revision `513f7a2d124215e7`; the local state is
  durable and re-checkable through the existing, secure loopback endpoint.
- **Negative/risk:** the readout endpoint depends on the local SQLite tracker
  file (`var/monitored_sources.sqlite3`); if that file is absent at app startup
  the tracker adapter creates it and read-back returns 404 until a revision is
  recorded. This is a dev-only observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, pinned to the
  fresh observed hash `513f7a2d124215e7`)
- `tests/test_monitored_sources_route.py` (readback route test module, pinned to
  the fresh observed hash `513f7a2d124215e7`)
- `var/monitored_sources.sqlite3` (runtime tracker, gitignored; records the new
  observed revision)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **8 passed (4 + 4), EXIT=0**
- Full: `pytest -v` → **219 passed, 0 failed, 222 warnings, EXIT=0**
- `ruff check tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **All checks passed!** (6 pre-existing errors in untracked
  `tests/test_corpus_mutations.py` left untouched per preservation rule)
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean** (historical LF→CRLF notices on Markdown only)
- Loopback readback via TestClient (authenticated, loopback, same-origin):
  `GET /api/monitored-sources/refundflow-opportunity` → **200**
  `{source_id: "refundflow-opportunity", observed_hash: "513f7a2d124215e7",
  observed_at: "2026-11-28T00:00:00+00:00", status: "current"}` and 404 for an
  unknown source.

## Commit / remote state

- One tested commit (readout regression tests), pushed to `dev`; remote `dev`
  read back and verified to equal the pushed SHA.

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model and the full
  evaluation/cache/security/evaluation suite for the fresh revision readout.
