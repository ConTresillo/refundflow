# RefundFlow roadmap

Status is updated only from verified work. See [AGENTS.md](../AGENTS.md) for the full product charter and acceptance criteria.

## Phase 1 — Foundation and design
- [ ] Confirm the existing venv, package boundaries, lock strategy, and local/offline baseline.
- [ ] HLD/LLD, domain model, safety/threat model, interfaces, ADR template, test/evaluation plan.
- [ ] Curate synthetic department policies and provenance; remove unsafe raw web snapshots from the source-of-truth corpus.

## Phase 2 — Policy catalog and change simulator
- [ ] Versioned policy catalog and department selection.
- [ ] Deterministic publish/amend/withdraw/expire simulation with an event timeline.

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

## Definition of done per slice
A slice is done only when focused tests and applicable full tests pass, artifacts/docs/decision record are updated, no unintended files or secrets are included, commit(s) are pushed, and remote `main` is verified.
