# RefundFlow roadmap

Status is updated only from verified work. See [AGENTS.md](../AGENTS.md) for the full product charter and acceptance criteria.

## Delivery model

- `main` is the stable, always-working line; `dev` is the integration branch for active phases. Scheduled/autonomous work stays on `dev`; no direct pushes or merges to `main`.
- CI runs on pull requests and pushes to both `main` and `dev`. Milestones reach `main` only through a PR after required checks pass (and review if configured).
- Deliver one small vertical slice at a time. Every phase ends with a locally runnable product demo, focused tests, and a short walkthrough. Repeated docs-only cycles do not count as product progress.
- This is a rigorous local enterprise simulation, not a production certification or deployment. Requirements are in [the enterprise RAG contract](architecture/enterprise-rag-contract.md).

## Phase 1 — Foundation and design
- [x] Confirm the existing venv, package boundaries, lock strategy, and local/offline baseline.
- [x] HLD/LLD, domain model, safety/threat model, interfaces, ADR template, test/evaluation plan, decision-log format.
- [x] Curate synthetic department policies and provenance; remove raw fetched snapshots.
- [x] Curated source-data validation and department-scoped selection surface.

## Phase 2 — Policy catalog and change simulator
- [x] Versioned policy catalog and department selection.
- [ ] Deterministic publish/amend/withdraw/expire simulation with an event timeline.

Verified: publication, withdrawal and explicit deterministic expiry transitions emit timeline events. Scheduled publish/amend activation, effective-date precedence/conflict explanation, rollback and index synchronization remain outstanding.
## Phase 3 — Real incremental RAG
- [x] Standalone SQLite FTS5 index module with stable chunk IDs, idempotent snapshots, changed/deleted-chunk retirement, rollback, and department/policy/version-scoped search (unit-tested).
- [x] Connect indexed retrieval to cited policy evidence with SQLite-authoritative status/effective-date filtering (single-version current catalog lookup; precedence/conflict selection remains open).
- [ ] After the Chroma security gate clears, implement the optional, rebuildable Chroma local vector-index adapter behind a narrow port, with explicit embeddings and no implicit model download.
- [ ] Content-hash diff ingestion; changed-file-only processing; idempotence, removal, rollback, and cross-store recovery.
- [ ] Labeled lexical/vector retrieval and freshness evaluation baseline.

Storage decision: SQLite is canonical for policy metadata, lifecycle, provenance and lexical FTS5. ChromaDB is the intended local vector-index adapter, behind a replaceable interface, but it is not installed or implemented. Adoption is gated on an upstream patched release and security review: PyPI currently lists 1.5.9, and GitHub/OSV advisories mark that release affected by unresolved critical/high issues. The embedding model/provider remains undecided; the vector index must be reproducible from SQLite and stable chunk IDs.

Ranking note: FTS5's built-in `rank` is relevance-ordered with a stable chunk-ID tie-break, verified by a multi-term test. This is lexical retrieval, not vector search.

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

## Phase 8 — CI/CD
- [x] GitHub Actions CI on pull requests and pushes to `main` and `dev`, with pinned actions, least privilege, Ruff linting, and supported Python/platform checks. The `dev` trigger was verified by run `44006540985` for `ac5b22c6086616352479739faa24e6856ce07e99`.
- [x] Manual main-only source-artifact delivery gated by the same CI checks.
- [x] Protect `main`: PR + strict four-job CI required; admin bypass, force-push and deletion disabled. PR approval count is 0 to avoid requiring a second repository collaborator.
- [ ] Select an approved deployment target and configure protected environment, health checks, and rollback before any external deployment.

Verified: CI run 43878742464 and source-delivery run 43889589858 both succeeded for commit `711973cc6b7b9a114a4c273b83e81115af4050e1`. The delivered tarball's SHA-256 matched its checksum file and its manifest named the same commit. Delivery is a source artifact, not production deployment.

## Integration and reuse
Inventory existing repo capabilities, official docs, maintained libraries and templates before implementation. Prefer wiring stable components over custom replacements; document any gap requiring adaptation. Keep frontend, backend, RAG, graph/MCP, evaluation and deployment modules independently testable, then verify their end-to-end integration. Piper TTS is the differentiating optional module.

## Enterprise delivery phases (supersede the earlier ordering below)

1. **Visible app shell and trusted identity:** local UI/API, OIDC-compatible identity boundary, explicitly labeled dev identity provider, health/readiness and a smoke test.
2. **IAM and document model:** tenants, users, groups, memberships, roles, permissions, document categories/scopes/grants, deny-by-default checks and audit.
3. **Permission-scoped RAG:** ACL filters before top-k and before model context, citations rechecked at return, cross-tenant non-interference and revocation tests. Keep Chroma gated.
4. **Async query service:** durable queue/worker, `202` plus run ID, progress/events, retry/idempotence/cancellation/recovery, async LangGraph and durable checkpointing.
5. **Bounded agent + HITL:** least-privilege tools, authorized interrupt/resume, audit trail, no real refund or external action.
6. **Messy-world hardening:** membership/ACL changes mid-run, corrupt/partial ingestion, queue/provider failure, prompt injection, cache isolation, observability, evaluations, recovery and local Compose.
7. **Interview-ready system design:** work through [the scenario bank](interviews/AI_SYSTEM_DESIGN_SCENARIOS.md), tie answers to working code/tests/measurements, and distinguish representative practice from verified employer-specific questions.

Each phase ends with a locally runnable demo. Design or library-selection spikes are bounded and should unblock implementation, not replace it.

## Current active slice

**Phase 1 — visible app shell and trusted identity.** Inspect current dependencies/runtime and compare maintained OIDC/dev-identity plus UI/API options. Then build the smallest runnable identity-aware page/API and smoke/security tests. Do not accept arbitrary user/role headers as authentication. If a required runtime is absent, report the exact blocker and local alternative. The Decision 0020 generation-manifest schema/status/recovery work remains backlog and can resume when it directly supports safe document ingestion; it is no longer the next product milestone.

Historical report from the prior design run: 34 tests passed (13 existing LangSmith deprecation warnings), Ruff on app/tests passed, compile check and `pip check` passed, and `git diff --check` was clean. Five pre-existing repo-wide Ruff issues in `conftest.py` and `scripts/make_policies.py` were not modified. These are not verification results for the current dev commit; rerun checks before claiming current status.

## Definition of done per slice
A slice is done only when its runnable behavior and security acceptance are exercised, focused and applicable full tests pass, docs/decision record include actual results, no secrets or unrelated files are included, the commit is pushed to `dev`, and remote `dev` is read back to verify the pushed SHA. `main` changes only through a PR after required checks pass.

## Engineering practice
Use autonomous, evidence-driven iteration rather than rigid ceremony: each slice starts with a clear outcome and ends with verified behavior, a versioned decision/run record, and a next step. Select test strategy per risk (top-down, bottom-up, sandwich/contract, unit/integration/e2e/evaluation). Final app behavior must be data-driven/configurable, not hard-coded to demo fixtures. Stubs are explicit test/provider-boundary replacements only; record fidelity limits and remaining real integration checks.
