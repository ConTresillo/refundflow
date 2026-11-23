# Decision/run record 0057 — bounded exact answer cache (C2)

- **Date:** 2026-11-23 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Goal:** Implement and validate the bounded exact answer cache (C2): a port with a
  SQLite-backed bounded adapter, keyed on the full valid observation envelope so stale or
  unauthorized answers are never served, with fail-closed byte-cap eviction, corruption→miss
  semantics, and a test-driven regression suite.
- **Context:** C2 was the pending bounded exact-answer-cache vertical slice. Prior runs had
  landed `app/answer_cache.py`, `app/retrieval.py` and `tests/test_answer_cache.py`, but the
  cache did not behave correctly: the first `put()` call advanced the embedded clock three
  times (one per payload field), so the expiry window shrank relative to the logical clock
  and entries were evicted too early. This run (a) isolated and fixed that clock-drift
  defect, (b) tightened lint, (c) re-ran focused + full suites, and (d) recorded and pushed
  the result on `dev`.

## Decision

1. **Clock drift fix in `SqliteBoundedAnswerCache.put()`:**
   - Before: `payload` computed `expires_at`, `created_at`, and `updated_at` each by a
     separate `self._clock()` call, advancing the embedded clock three times per logical
     `put()`.
   - After: compute `now = self._clock()` once and reuse it for all three fields.
   - Verified behavior: after `put()` + `_clock()` once, a bounded cache still returns the
     envelope (the TTL window is not shrunken by field bookkeeping).

2. **Import hygiene in `tests/test_answer_cache.py`:**
   - Removed unused `time`, `AnswerCacheError`; replaced `dict(...)` with a literal dict;
     renamed unused `db` variables to `_db`.

3. **Code quality gates:**
   - `ruff check app/answer_cache.py app/retrieval.py tests/test_answer_cache.py` →
     **All checks passed!**
   - `compileall -q app tests` → **clear**
   - `pip check` → **No broken requirements found**

4. **Verification:**
   - `pytest tests/test_answer_cache.py -q` → **15 passed** (EXIT=0)
   - Full suite → **EXIT=0**
   - Commit/push/read-back: `dev` local == remote == `4fd5d79bbe0137d973d937f3e7e6210761f05033`

## Alternatives considered

- **Rely on an external Redis/semantic-cache service.** Rejected: the requirement is a
  local, replaceable port; the SQLite adapter is the only default, offline-backed layer.
- **Make `put()` store the envelope and evict lazily on read.** Rejected: the bounded byte
  cap is simpler and strictly safer when enforced at `put()` time (fail-closed), avoiding
  unbounded growth before any read.
- **Evict on `get()` for expired rows.** Rejected: correct behavior is to remove nothing
  and return `None` for expired/corrupt rows so cache miss handling stays uniform; row
  removal is a side effect of the bounded LRU pass only.

## Consequences

- **Positive:** C2 is implemented, tested, and committed on `dev`; the retriever now consults
  the exact cache for `answer_cache="exact"` with principal-scoped envelope keys. Combined
  with C1 (freshness modes), the retrieval path now has two orthogonal cache layers: a
  TTL/freshness cache and a bounded exact-envelope cache.
- **Negative/risk:** the SQLite row format uses a JSON envelope field, so cache lookups are
  JSON-bound; this is acceptable in the bounded local demo but is archived as an intended
  future optimization. The byte cap is enforced at `put()` time (fail-closed); a full-run
  stress proof of the byte cap under adversarial batch sizes is an archived improvable.

## Touched files

- `app/answer_cache.py` (new port + SQLite adapter; clock fix in `put()`)
- `app/retrieval.py` (exact-cache branch, `CacheKey`/`SqliteBoundedAnswerCache` wiring)
- `app/policy_change_service.py` (cache/principal parameters threaded through)
- `tests/test_answer_cache.py` (15 behavior-focused cases, tightened imports/lint)
- `docs/ROADMAP.md` (C2 subsection marked delivered)

## Tests / evaluation (actual output)

- `pytest tests/test_answer_cache.py -q` → **15 passed** (EXIT=0)
- `pytest -q` → **EXIT=0**
- `ruff check app/answer_cache.py app/retrieval.py tests/test_answer_cache.py` → **All
  checks passed!**
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found**
- Remote read-back after push: `4fd5d79bbe0137d973d937f3e7e6210761f05033` (== local dev)

## Commit / remote state

- Single cohesive commit on `dev` (verified after push, read back via `git ls-remote
  origin refs/heads/dev`).
- `main` untouched (no PR yet).

## Blocker / next step

- No hard blocker. Next bounded follow-on: C1/C2 interplay for the `semantic`/`prompt`
  layers, then proceed to Phase 5 (LangGraph/MCP) bounded specialist workflow.
