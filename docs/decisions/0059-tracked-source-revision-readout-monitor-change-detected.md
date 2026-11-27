# Decision/run record 0059 — Tracked-source revision freshness readout (MONITOR_CHANGE_DETECTED)

- **Date:** 2026-11-27 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `b506e1f5a1521164` to `fc9fc674b8a7439e`.

## Goal

Deliver one demonstrable, user-visible increment that makes the monitoring
tracked source's revision freshness visible and re-checkable locally:

1. Persist the new observed revision hash (`fc9fc674b8a7439e`) into the durable,
   replaceable SQLite tracker (`app/monitored_sources.SQLiteMonitoredSources`);
2. Expose a thin, authenticated, read-only `GET /api/monitored-sources/{source_id}`
   route that returns the durable observed revision (or 404 for an unknown source,
   401 unauthenticated, 403 non-loopback);
3. Pin the regression tests to the new fresh revision and prove readback;
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable.

## Decision

1. **Persist into the existing app SQLite.** Recorded the fresh revision
   `fc9fc674b8a7439e` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`) via `SQLiteMonitoredSources.record()`.
   Persistence is fail-closed (invalid source_id / empty hash rejected with
   `ValueError`).
2. **Expose a one-endpoint read-only readout** wired into `create_app()`
   (already present), sharing the app's existing dev-loopback boundary,
   origin/CSRF enforcement, and deny-by-default posture. Unknown source → 404;
   unauthenticated → 401; non-loopback → 403.
3. **Test-first regression coverage.** Pinned both the route writeback test and
   the tracker unit test to the new fresh revision hash `fc9fc674b8a7439e`.
4. **Do not touch `main` or the policy corpus**; this is a dev-loopback,
   read-only observability endpoint on `dev`.

## Consequences

- **Positive:** the pilot/operator can now see that the external tracked source
  advanced to the freshest revision `fc9fc674b8a7439e`; the local state is
  durable and re-checkable through the existing, secure loopback endpoint.
- **Negative/risk:** the new endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if that file is absent at app startup the
  tracker adapter creates it and the read-back returns 404 until a revision is
  recorded. This is a dev-only observability surface, not a product contract.

## Touched files

- `tests/test_monitored_sources.py` (revision regression test, pinned to the
  fresh observed hash `fc9fc674b8a7439e`)
- `tests/test_monitored_sources_route.py` (readback route test module, pinned to
  the fresh observed hash `fc9fc674b8a7439e`)
- `var/monitored_sources.sqlite3` (runtime tracker, gitignored; records the new
  observed revision)

## Tests / evaluation (actual output)

- Focused: `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **8 passed (4 + 4), EXIT=0**
- Full: `pytest -v` → **219 passed, 0 failed, 222 warnings, EXIT=0**
- `ruff check app tests/test_monitored_sources.py tests/test_monitored_sources_route.py`
  → **All checks passed!** (6 pre-existing errors in untracked
  `tests/test_corpus_mutations.py` left untouched per preservation rule)
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean** (only expected LF→CRLF notices on historical
  Markdown, not whitespace errors)
- Loopback readback via TestClient (authenticated, loopback, same-origin):
  `GET /api/monitored-sources/refundflow-opportunity` → **200**
  `{source_id: "refundflow-opportunity", observed_hash: "fc9fc674b8a7439e",
  observed_at: "<UTC>", status: "current"}` and 404 for an unknown source.

## Commit / remote state

- Two commits this run (test regressions; decision record), pushed to `dev`,
  remote `dev` read back to verify the pushed SHA (see commit notes).

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model and the full
  evaluation/cache/security/evaluation suite for the fresh revision readout,
  plus confirmation of the remote `dev` SHA after push.
