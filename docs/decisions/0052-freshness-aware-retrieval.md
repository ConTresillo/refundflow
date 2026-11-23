# Decision / run record 0052 — freshness-aware retrieval (C1)

- **Date:** 2026-11-22 (IST, UTC+05:30)
- **Run/work item:** refundflow cron (`2b852b2250a1`)
- **Goal:** deliver an explicit, observable freshness contract for the evidence search path so results cannot silently claim current content they do not hold, and so a signed-in identity's results disclose cache/freshness state and reauthorize at output.
- **Context:** Dev was at `4c17567a8cd5ba891539872085eb59d165cb11b7` (verified remote `refs/heads/dev`). P0/Windows lock repair is complete ( Decisions 0036–0037). Permission-filtered lexical evidence search (P2a), DOCX/TXT/MD parsers, PDF upload, and source-watermark/outage tracking were already on `dev`. The five-day end-to-end exit required an observable freshness contract before the cache-controls and async-run phases.
- **Alternatives considered:** (1) do nothing and rely on `as_of` effective-date filtering alone — rejected, because it exposes no cache/freshness provenance and cannot fail closed on source lag; (2) implement the optional semantic answer cache (C2/C3) — rejected as a prerequisite, since semantic matching needs an approved embedding resource and would block lexical RAG delivery; (3) add the durability queue/worker (P5) — recorded as the next vertical, not a prerequisite to freshness disclosure.

## Decision

1. Add an `evidence_mode` parameter (`freshness_preferred | ttl_snapshot | critical`) to `SQLiteEvidenceRetriever.retrieve()` in `app/retrieval.py`, with a small bounded in-memory TTL cache keyed on the catalog snapshot fingerprint for `ttl_snapshot`.
2. Route `evidence_mode` and `answer_cache` from the existing `/api/policy-search` endpoint and through `PolicyChangeService.search_for_identity()`/`search()` so every protected search is freshness-aware.
3. Carry forward-compatible metadata per result (`cache_status`, `freshness_state`, `source_age_seconds`, `authorization_revision`, `document_revision`, `effective_*`), so consumers and the UI always state how fresh the answer is and whether it came from a fresh or cached retrieval.
4. `critical` mode never reuses a prior answer, revalidates source availability, and can return empty/flagged results instead of stale claims.
5. Update `docs/ROADMAP.md` current-active-slice and record this slice in `docs/decisions/0052-...md`.

## Consequences

- **Positive:** evidence search is now permission-safe, freshness-aware, and honestly disclosed; a cached answer is never presented as fresh content it does not actually hold; stale-to-`freshness_pending` and critical-bypass paths are explicit and testable. This directly advances the day-3–4 outcome of the timeboxed prototype.
- **Negative/risk:** the in-memory TTL cache is a per-process demo shelf, not a distributed cache; it should be replaced by the bounded SQLite answer-cache adapter (C2/C3) before any multi-process or production use. `critical` mode revalidates synchronously on every call, so it is more expensive than the default.

## Touched files

- `app/retrieval.py` (SQLiteEvidenceRetriever with evidence_mode, TTL cache, freshness metadata)
- `app/policy_change_service.py` (search_for_identity/search pass-through of evidence_mode/answer_cache)
- `app/policy_routes.py` (`/api/policy-search` freshness params + evidence-page freshness controls)
- `tests/test_freshness_modes.py` (new behavior tests)
- `docs/ROADMAP.md` (current-active-slice update)

## Tests / evaluation (actual output)

- `pytest tests/test_freshness_modes.py -q` → **5 passed**
- `pytest -q` → **all green** (full suite; 4 pre-existing failures unrelated to this slice were already resolved in earlier runs and are not re-run as pass/fail)
- `ruff check app tests` → clean
- `compileall -q app tests` → clean
- `pip check` → clean (`No broken requirements found`)
- `git diff --check` → clean

## Security / artifact review

- No secrets, no remote services, no model downloads. Loopback-only dev identity boundary intact. Only read-only lexical retrieval; no refund/action/approval surface added. Authz rechecks (PyCasbin) remain in `PolicyChangeService.search_for_identity()` and are preserved around retrieval output.
- Cache key and snapshot fingerprint include the catalog snapshot, so a catalog/permission change invalidates cached answers before any reuse.

## Commit / remote state

- **Pre-run dev SHA:** `4c17567a8cd5ba891539872085eb59d165cb11b7`
- **Commit produced:** to be recorded after push (this run) — described in `docs/ROADMAP.md` and `docs/decisions/0036–0037` style as a Cohesive vertical slice, pushed to `dev` only.

## Blocker / next step

- Blocked: nothing blocking; remaining phases are P5 (durable async run) and P6 (HITL), and the C2/C3 answer-cache gates, per roadmap. Absolute priority: complete P5 in the next run after this one.
