# ADR 0001: Local-first modular RefundFlow scope

- Status: accepted as initial direction; details remain revisable via superseding ADRs.
- Context: demonstrate department-specific refund workflows, real incremental policy RAG, changing policies, human approval, MCP, evaluation and observability without early paid-service dependencies.
- Decision: build a local-first full-stack simulation with replaceable adapters, deterministic mock LLM only, real local retrieval/ingestion/MCP/checkpoint/cache, and explicit decision records.
- Alternatives: hosted vector database/LLM/MCP services from day one; rejected to avoid credentials, cost, network coupling and blocked setup.
- Consequences: strong offline reproducibility; local storage choices and mock-LLM limits must be documented; request owner approval before introducing remote services or credentials.
- Acceptance: RED/GREEN tests per slice, evaluation evidence, max two commits per run, clean tree and verified remote head.
