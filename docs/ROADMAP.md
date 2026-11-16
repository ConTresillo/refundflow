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

Verified: publication, withdrawal and explicit deterministic expiry transitions emit timeline events. Scheduled publish/amend activation, effective-date precedence/conflict explanation, rollback and index synchronization remain outstanding.
## Phase 3 — Real incremental RAG
- [x] Standalone SQLite FTS5 index module with stable chunk IDs, idempotent snapshots, changed/deleted-chunk retirement, rollback, and department/policy/version-scoped search (unit-tested; not yet connected to the application retrieval path).
- [ ] Connect indexed retrieval to complete, cited policy evidence with SQLite-authoritative status/effective-date filtering.
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
- [x] GitHub Actions CI on pull requests and `main`, with pinned actions, least privilege, Ruff linting, and supported Python/platform checks.
- [x] Manual main-only source-artifact delivery gated by the same CI checks.
- [ ] Select an approved deployment target and configure protected environment, health checks, and rollback before any external deployment.

Verified: CI run 43878742464 and source-delivery run 43889589858 both succeeded for commit `711973cc6b7b9a114a4c273b83e81115af4050e1`. The delivered tarball's SHA-256 matched its checksum file and its manifest named the same commit. Delivery is a source artifact, not production deployment.

## Integration and reuse
Inventory existing repo capabilities, official docs, maintained libraries and templates before implementation. Prefer wiring stable components over custom replacements; document any gap requiring adaptation. Keep frontend, backend, RAG, graph/MCP, evaluation and deployment modules independently testable, then verify their end-to-end integration. Piper TTS is the differentiating optional module.

## Next slice

- [ ] Define narrow catalog, lexical-index, vector-index, and embedding-provider contracts; connect the SQLite FTS5 index to cited policy evidence with deterministic `as_of`/freshness filtering and regression tests. Keep the Chroma port dependency-free until the package security gate clears. Before adding Chroma, verify a patched upstream release and review advisories/dependency lock; if none is available, keep the local lexical path and compare a safer local vector alternative. Do not download an embedding model implicitly; select one only after measuring quality, license, size and platform fit. External deployment remains blocked on an owner-approved target.


## Definition of done per slice
A slice is done only when focused tests and applicable full tests pass, artifacts/docs/decision record are updated, no unintended files or secrets are included, commit(s) are pushed, and remote `main` is verified.

## Engineering practice
Use autonomous, evidence-driven iteration rather than rigid ceremony: each slice starts with a clear outcome and ends with verified behavior, a versioned decision/run record, and a next step. Select test strategy per risk (top-down, bottom-up, sandwich/contract, unit/integration/e2e/evaluation). Final app behavior must be data-driven/configurable, not hard-coded to demo fixtures. Stubs are explicit test/provider-boundary replacements only; record fidelity limits and remaining real integration checks.
