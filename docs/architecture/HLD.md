# High-level design — draft

## Goals and constraints
Full-stack refund-support simulation; department-specific policy sets; policy changes and incremental RAG; bounded LangGraph workflow with human approval; local MCP; deterministic mock LLM; local-first operation; Docker/Compose. See [project charter](../../AGENTS.md).

## Proposed logical components
- Web UI: case intake, department/policy selection, evidence and workflow timeline, approval inbox, ingestion/cache/quality status.
- API/application layer: validation, tenant-scoped identity/authentication, authorization policy checks, durable run commands, read models and audit boundary.
- LangGraph workflow: typed state, bounded specialist nodes, retrieval, MCP-mediated tool execution, human interrupt/resume.
- Policy domain: catalog/version/effective dates/provenance, chaos simulator, ingestion coordinator.
- Retrieval: content normalization, stable chunk identity, local lexical/vector adapter, citations and freshness filters.
- Cache: optional provider prompt-cache metadata plus independent semantic cache with policy-scoped invalidation.
- Persistence: policy metadata, chunks/index, graph checkpoints, approvals, audit events and evaluation results.
- Enterprise identity/access: OIDC adapter, tenant/user/group/role membership, document grants and authorization-engine port; exact engine selection is an implementation spike, not a hand-rolled policy system.
- Background execution: durable queue, worker, run-status/event store and async graph/checkpointer adapter; interactive query execution is distinct from synchronous ingestion/index transactions.
- MCP: local server/adapters for safe demo tools; no remote service by default.
- Observability/evaluation: run and trace IDs, structured redacted events, reproducible datasets and metrics.

## Initial deployment boundary
The initial local stack uses SQLite as the authoritative policy catalog/lifecycle store and SQLite FTS5 for dependency-free lexical search. ChromaDB is the intended replaceable local vector-index adapter, but adoption is gated: the latest available ChromaDB release (1.5.9) is listed as affected by unresolved critical/security advisories, so do not install or expose it until a patched release passes review. Keep any eventual Chroma dependency in an optional `vector` extra. Do not let Chroma choose or download an embedding model implicitly: the embedding provider is an explicit, fingerprinted boundary. Chroma's persistent client is documented for local development/testing, while production use should be server-backed; this is an adapter migration, not a claim that a local folder is production-grade. SQLite/Chroma are not yet integrated in the app. See [Decision 0012](../decisions/0012-local-sqlite-chroma-boundary.md). Piper TTS is optional and must degrade gracefully.

## Enterprise query and access boundary
The target architecture, async API contract, identity/RBAC and document-scope model, threat cases, and phased acceptance are in [the enterprise RAG contract](enterprise-rag-contract.md). They are not implemented yet: current code has SQLite/FTS ingestion and retrieval only, with no API, identity, authorization engine, queue, or LangGraph query workflow. In particular, a reported “sync transaction” is the policy/index sync, not an interactive query design.

## Open decisions
Embedding model/provider and its licensing/resource budget, a patched and security-reviewed Chroma release, frontend framework, OIDC dev-provider/library, authorization engine (compare Casbin and OpenFGA), durable queue/broker, exact evaluation dataset/metrics, and workflow agent boundaries must be resolved through small compatibility/security spikes before becoming hard dependencies. SQLite remains selected for canonical policy metadata and lexical search; Chroma remains the vector-store direction, conditional on the security gate.

## Integration approach
Treat frontend, API, RAG/policy, LangGraph/MCP, evaluation and deployment as cohesive modules with explicit contracts. Reuse established libraries/templates and compose them; parallel implementation is acceptable only after contracts are agreed. Prefer wiring stable components over rewriting them. TTS is the differentiating optional local adapter, kept isolated from core policy decisions.
