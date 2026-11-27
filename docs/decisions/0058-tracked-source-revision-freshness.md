# Decision/run record 0058 — Tracked-source revision freshness readout

- **Date:** 2026-11-27 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Trigger:** The monitored external source `refundflow-opportunity` flipped its
  observed revision hash from `7b5aabfbae4b37e7` to `6938664fb626845e`.

## Goal

Deliver one demonstrable, user-visible increment that makes the monitoring the
tracked source's revision freshness visible and re-checkable locally:

1. Persist the new observed revision hash (`6938664fb626845e`) into the durable,
   replaceable SQLite tracker (`app/monitored_sources.SQLiteMonitoredSources`);
2. Expose a thin, authenticated, read-only `GET /api/monitored-sources/{source_id}`
   route that returns the durable observed revision (or 404 for an unknown source,
   401 unauthenticated, 403 non-loopback);
3. Add focused regression tests proving the fresh revision is recorded, read back,
   and fail-closed,
4. Keep the app green (focused + full suite), lint/compile/pip clean.

The tracked source is external (`app/monitored_sources.py` — never fetches,
mutates the policy corpus, never performs external actions); this work makes its
new revision durable and observable.

## Decision

1. **Persist into the existing app SQLite.** Recorded the fresh revision
   `6938664fb626845e` with `status = 'current'` into the app's canonical local
   SQLite (`var/monitored_sources.sqlite3`). The persistence is fail-closed
   (invalid source_id / empty hash rejected with `ValueError`).
2. **Expose a one-endpoint read-only readout wired into `create_app()`**, sharing
   the app's existing dev-loopback boundary, origin/CSRF enforcement, and
   deny-by-default posture. Unknown source → 404; unauthenticated → 401;
   non-loopback → 403.
3. **Test-first regression coverage.** Added `tests/test_monitored_sources.py` (pin
   the fresh hash as the current observed revision) and
   `tests/test_monitored_sources_route.py` (4 route tests: 401/404/200-with-fresh
   revision/403-cross-origin).
4. **Do not touch `main` or the policy corpus**; this is a dev-loopback, read-only
   observability endpoint on `dev`.

## Consequences

- **Positive:** the pilot/operator can now see that the external tracked source
  advanced to the freshest revision; the local state is durable and re-checkable
  through an existing, secure loopback endpoint. The full suite stays green.
- **Negative/risk:** the new endpoint depends on the local SQLite tracker file
  (`var/monitored_sources.sqlite3`); if that file is absent at app startup the
  tracker adapter creates it and the read-back returns 404 until a revision is
  recorded. This is a dev-only observability surface, not a product contract.

## Touched files

- `app/web_runtime.py` (route wiring + tracker constant + `create_app` state)
- `app/monitored_sources.py` (tracker module, already present, reviewed/tested)
- `tests/test_monitored_sources.py` (revision regression test, refreshed to the
  fresh observed hash `6938664fb626845e`)
- `tests/test_monitored_sources_route.py` (new readback route test module)

## Tests / evaluation (actual output)

- `pytest tests/test_monitored_sources.py tests/test_monitored_sources_route.py
  -q --tb=no -p no:cacheprovider` → **8 passed** (EXIT=0)
- `pytest -q --tb=no -p no:cacheprovider` → **215 passed, 225 warnings**
- `ruff check app tests` → **All checks passed!**
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found.**
- `git diff --check` → **clean** (only expected LF→CRLF normalization notices on
  historical Markdown files, not whitespace errors)
- Loopback readback via `TestClient` (authenticated, loopback, same-origin):
  `GET /api/monitored-sources/refundflow-opportunity` → **200**
  `{source_id: "refundflow-opportunity", observed_hash: "6938664fb626845e",
  observed_at: "<UTC>", status: "current"}` and 404 for an unknown source.

## Commit / remote state

- Two commits this run (code+tests; decision record), pushed to `dev`, remote
  `dev` read back to verify the pushed SHA (see the decision record commit notes).

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7a audit/read-model and the full
  evaluation/cache/security/evaluation suite for the fresh revision readout, plus
  confirmation of the remote `dev` SHA after push.
