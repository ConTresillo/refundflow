# RefundFlow roadmap

Status is updated only from verified work. See [AGENTS.md](../AGENTS.md) for the full product charter and acceptance criteria.

## Phase 1 — Foundation and design
- [x] Confirm the existing venv, package boundaries, lock strategy, and local/offline baseline.
- [x] HLD/LLD, domain model, safety/threat model, interfaces, ADR template, test/evaluation plan, decision-log format.
- [x] Curate synthetic department policies and provenance; remove raw fetched snapshots.
- [x] Curated source-data validation and department-scoped selection surface.

## Phase 2 — Policy catalog and change simulator
- [x] Versioned policy catalog and department selection.
- [ ] Deterministic publish/amend/withdraw/expire simulation with an event timeline.

The current catalog persists immutable version snapshots and publication events in SQLite. Withdrawal is now represented as an append-only event and the current snapshot is marked withdrawn; scheduled changes, amendment/version publishing, expiry/effective-date activation and ingestion/retrieval integration remain outstanding.

## Phase 3 — Real incremental RAG
- [ ] Stable chunk IDs, real local retrieval, citations, freshness/version filters.
- [ ] Content-hash diff ingestion; changed-file-only processing; idempotence, removal, rollback.
- [ ] Labeled retrieval/freshness evaluation baseline.

## Phase 4 — Caching and observability
- [ ] Separate prompt-cache concept from real semantic cache.
- [ ] Scoped keys, policy/version/config invalidation, stale-evidence guard.
- [ ] Structured privacy-safe traces, run IDs, counters and event records.

## Phase 5 — LangGraph and MCP
- [ ] Bounded specialist workflow, real state/checkpointing, interrupt/resume approval.
- [ ] Local MCP tool server/adapters; deterministic mock LLM only.
- [ ] Audit log and safety/evaluation cases.

## Phase 6 — Full stack
- [ ] API and UI for department/policy selection, cases, evidence, approvals and system status.
- [ ] Accessibility, error/loading/empty states, integration tests.

## Phase 7 — Release and evaluation
- [ ] Full evaluation matrix, security review, Docker/Compose, operational guide.
- [ ] Optional Piper adapter (no automatic model download).
- [ ] Interview walkthrough and reproducible demo.

## Integration and reuse
Inventory existing repo capabilities, official docs, maintained libraries and templates before implementation. Prefer wiring stable components over custom replacements; document any gap requiring adaptation. Keep frontend, backend, RAG, graph/MCP, evaluation and deployment modules independently testable, then verify their end-to-end integration. Piper TTS is the differentiating optional module.

## Definition of done per slice
A slice is done only when focused tests and applicable full tests pass, artifacts/docs/decision record are updated, no unintended files or secrets are included, commit(s) are pushed, and remote `main` is verified.

## Engineering practice
Use autonomous, evidence-driven iteration rather than rigid ceremony: each slice starts with a clear outcome and ends with verified behavior, a versioned decision/run record, and a next step. Select test strategy per risk (top-down, bottom-up, sandwich/contract, unit/integration/e2e/evaluation). Final app behavior must be data-driven/configurable, not hard-coded to demo fixtures. Stubs are explicit test/provider-boundary replacements only; record fidelity limits and remaining real integration checks.
