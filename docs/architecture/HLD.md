# High-level design — draft

## Goals and constraints
Full-stack refund-support simulation; department-specific policy sets; policy changes and incremental RAG; bounded LangGraph workflow with human approval; local MCP; deterministic mock LLM; local-first operation; Docker/Compose. See [project charter](../../AGENTS.md).

## Proposed logical components
- Web UI: case intake, department/policy selection, evidence and workflow timeline, approval inbox, ingestion/cache/quality status.
- API/application layer: validation, orchestration requests, read models and auth boundary (local demo first).
- LangGraph workflow: typed state, bounded specialist nodes, retrieval, MCP-mediated tool execution, human interrupt/resume.
- Policy domain: catalog/version/effective dates/provenance, chaos simulator, ingestion coordinator.
- Retrieval: content normalization, stable chunk identity, local lexical/vector adapter, citations and freshness filters.
- Cache: optional provider prompt-cache metadata plus independent semantic cache with policy-scoped invalidation.
- Persistence: policy metadata, chunks/index, graph checkpoints, approvals, audit events and evaluation results.
- MCP: local server/adapters for safe demo tools; no remote service by default.
- Observability/evaluation: run and trace IDs, structured redacted events, reproducible datasets and metrics.

## Initial deployment boundary
One local Docker Compose deployment should run UI/API and local persistence without requiring a paid cloud service. Exact database/vector-store choice is an ADR after a small comparison. Piper TTS is optional and must degrade gracefully. Production topology is a separate design, not implied by local Compose.

## Open decisions
Storage/index selection, frontend framework, authentication for the local demo, exact evaluation dataset/metrics and workflow agent boundaries must be decided in ADRs with tests and trade-offs before they become hard dependencies.
