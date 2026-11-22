# Decision / run record 0056 — P6 verified + P7a audit/read model (active slice)

- **Date:** 2026-11-23 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder (MONITOR_CHANGE_DETECTED run)
- **Goal:** Advance the P6 bounded simulated decision + human-in-the-loop workflow (verified, delivered) and deliver the P7a immutable audit/read-model slice (command_dedupe, append-only DB triggers, redacted audit read API).
- **Context:** dev was at a P6-delivered tip with the roadmap's `Current active slice` section stale (said "P6 next"; P6 is committed). This run (a) verified the P6 slice end to end and recorded it in the roadmap, and (b) delivered the bounded P7a slice on top of P6.

## Decision

1. **Verify P6 end-to-end** before recording it as delivered. Focus suite:
   - `tests/test_decision_graph.py` (9), `tests/test_decision_run.py` (6), `tests/test_decision_workbook.py` (2), `tests/test_mcp_tools.py` (7) → **24 passed**.
   - Full suite: **177 passed, 169 warnings** (EXIT=0).
   - CI-equivalent: `ruff check app tests` clear on touched paths; `compileall -q app tests` clear; `pip check` clear.
   - Record P6 as delivered in `docs/ROADMAP.md` (active-slice section + P6 checkboxes).

2. **Deliver P7a audit/read model** (this run's new micro-slice):
   - `app/decision_run.py` — `command_id` column and per-command idempotency on `create_run`, `record_graph_output`, `approve_run`, `cancel_run`, `append_event`; fail-closed duplicate/command-conflict rejection; append-only `decision_run_events` enforced by DB triggers (`trg_decision_run_events_no_update`, `trg_decision_run_events_no_delete`, `trg_decision_runs_command_id_required`).
   - `app/decision_run_routes.py` — `command_id` on the create/approve/cancel payloads and on the new redacted GET `/api/decision-runs/{run_id}/audit` endpoint (sensitive `details` fields redacted).
   - `tests/test_audit.py` — 9 behavior-focused tests: append-only trigger rejection, command_id reassignment rejection, null→set command_id, idempotent same-command approval, conflicting-command rejection, transaction rollback on audit guard, and redacted audit read with tenant-denial.

## Alternatives considered

- **Skip P7a and move to P7b/redacted observability.** Rejected: the immutable audit/read model is the prerequisite for a redacted UI; the audit/read model must exist and be verified before a redacted timeline UI can be built on it.
- **Add command_id to the graph state only, not the DB.** Rejected: async LangGraph checkpoint/resume must survive worker loss, so the dedupe/ref state must live in the durable outbox (SQLite), not in-memory checkpoint channels.

## Consequences

- **Positive:** the decision-run workflow now has a durable, append-only audit trail with command dedupe; the redacted audit read API is exercised. Backward-compatible with existing callers (command_id is optional and nullable).
- **Risk:** the DB triggers are application-level, not WORM assurance (a host admin can still edit SQLite). The roadmap records this limitation explicitly.

## Touched files

- `app/decision_run.py` (new `command_id`, append-only triggers, idempotent approval/cancel)
- `app/decision_run_routes.py` (command_id payloads, cancel payload, redacted audit endpoint)
- `tests/test_audit.py` (9 new P7a behavior tests)
- `docs/ROADMAP.md` (active slice, P6 delivered, P7a in progress)

## Tests / evaluation (actual output)

- `pytest tests/test_audit.py -q` → **9 passed** (EXIT=0)
- `pytest tests/test_decision_graph.py tests/test_decision_run.py tests/test_decision_workbook.py tests/test_mcp_tools.py -q` → **24 passed** (EXIT=0)
- Full suite: **177 passed, 169 warnings** (EXIT=0)
- `ruff check tests/test_audit.py app/decision_run.py app/decision_run_routes.py` → **All checks passed**
- `compileall -q app tests` → **clear**
- `pip check` → **No broken requirements found**

## Commit / remote state

- Single cohesive commit on `dev` (verified after push, read back `git ls-remote origin refs/heads/dev`).
- `main` untouched (no PR yet).

## Blocker / next step

- No hard blocker. Next bounded follow-on: P7b redacted observability/recovery and the remaining P7a "redacted timeline UI" surface (the API endpoint is delivered; the UI is a thin client over it).
