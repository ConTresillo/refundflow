# Decision / run record 0054 — durable async evidence run (P5)

- **Date:** 2026-11-22 (IST, UTC+05:30)
- **Run/work item:** refundflow cron (scheduled autonomous builder)
- **Goal:** deliver the remaining end-to-end vertical of the five-day exit — a
  durable, idempotent, resumable async evidence-run pipeline (P5) with a real queue /
  worker, persistent `202` run IDs, progress/events, retry/idempotence/cancellation,
  and an async LangGraph graph with durable SQLite checkpointing.
- **Context:** dev was at `b5428da94504b5e04ff812922a201a2d85561a46` (verified remote
  `refs/heads/dev`). P0 lock/CI repair (Decisions 0036–0037) and the permission-safe
  lexical evidence slice (P2a/P2b), incremental document-format adapters, PDF upload,
  and source-watermark/outage tracking were already on `dev`. The next unmet slice was
  P5 durable async evidence run.
- **Alternatives considered:**
  1. No durable queue; use FastAPI `BackgroundTasks` only — rejected: not durable,
     cannot resume after worker loss, contradicts the contract's explicit queue rule.
  2. Use an external message broker — rejected: no approved external service; local
     SQLite outbox is durable and self-contained.
  3. Skip queue/worker and ship a synchronous evidence endpoint — rejected: does not
     meet the async-first, dureable, cancelable HITL-readiness requirement.
- **Decision:** implement P5 as a cohesive vertical slice:
  1. `app/evidence_run.py` — SQLite outbox/run tables, idempotent `enqueue` (payload-hash
     scoped run IDs), claim/requeue, cancel, crash-recovery `requeue_stale`, append-only
     `evidence_run_events`; typed `EvidenceRunState` and typed `EvidenceRun` / `Result`.
  2. `app/evidence_queue.py` — `EvidenceQueue` with bounded retries, cancellation,
     dead-lettering, and restart-safe claim over the shared SQLite outbox.
  3. `app/evidence_runner.py` — async LangGraph `authorize -> retrieve -> terminal`
     evidence graph with `AsyncSqliteSaver` durable checkpointing; fail-closed
     authorization (`scope` returns allowed policy IDs; empty scope denies without
     retrieving).
  4. `app/policy_run_routes.py` — `POST /api/evidence-runs` (202 + run id), `GET
     /api/evidence-runs/{id}`, `GET /api/evidence-runs`, `POST
     /api/evidence-runs/{id}/cancel` (202, fail-closed).
  5. `app/web_runtime.py` — wire evidence-run routes at startup; create the
     `AsyncSqliteSaver` checkpointer on startup; close the async connection on
     shutdown (resource-leak fix).
- **Consequences:**
  - **Positive:** durable async evidence runs now work end-to-end: enqueue -> run ->
    authorize (deny when no grant) -> (or retrieve) -> terminal; idempotent re-submission
    returns the same run id; cancel, revoke, and crash/recovery all converge. Full suite
    green.
  - **Negative/risk:** the in-process `AsyncSqliteSaver` is for a single-process demo;
    a real distributed worker/checkpointer (or a maintained SQLite adapter) is needed
    before multi-host use. Behavioral tests exercise real SQLite/FTS and graph
    checkpointing; the mock-LLM remains the explicit fake boundary.
- **Touched files**
  - `app/evidence_run.py` (new)
  - `app/evidence_queue.py` (new)
  - `app/evidence_runner.py` (new)
  - `app/policy_run_routes.py` (new + cancel `202` fix)
  - `app/web_runtime.py` (evidence-run wiring + shutdown close fix)
  - `app/policy_workspace.py` (build_evidence_scope used by the graph scope)
  - `app/retrieval.py` (`PolicyEvidence.to_dict` needed by checkpointer serialization)
  - `tests/test_evidence_runs.py` (behavior tests)
- **Tests / evaluation (actual output)**
  - `pytest tests/test_evidence_runs.py -q` → **7 passed** (idempotent enqueue,
    cancel/fail-closed, revoked-actor denial, crash-recovery requeue, CLI dedup)
  - `pytest -q` → **131 passed, 161 warnings** (EXIT=0)
  - `ruff check app tests --output-format=concise` → 35 findings, all pre-existing in the
    prior run's uncommitted work (not added by this run); affected imports/harness
    files; no new lint errors introduced by this slice.
  - `compileall -q app tests` → clean
  - `pip check` → `No broken requirements found.`
  - `git diff --check` → clean
- **Security / artifact review**
  - No secrets, no remote services, no model downloads. Loopback-only dev identity
    boundary intact. Authz rechecks (PyCasbin) verified around enqueue, graph authorize,
    and output; revoked queued actor fails closed. No real refunds/actions.
- **Commit / remote state**
  - Pre-run dev SHA (local == remote): `b5428da94504b5e04ff812922a201a2d85561a46`
  - Commit 1: `0216db0` — "P5: add durable async evidence run queue and LangGraph pipeline"
  - Commit 2: `726c53c` — "P5: add durable evidence-run endpoint tests"
  - Pushed to `refs/heads/dev`; read back `726c53c19159763e00eca5420b5363a135bfd3b7`
    equals local HEAD.
- **Blocker / next step**
  - No hard blocker. Remaining: P6 zero-turn HITL approval/resume, plus C2/C3 answer
    cache gates. The QA loopback smoke and the two verified commits are the end of this
    run. Next: wire P6 bounded simulated decision + human-in-the-loop approvals.
