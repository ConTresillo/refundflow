# Low-level design — draft

## Core records
Policy: policy_id, department_id, version, title, status, effective_from/to, source URI, provenance/license, content_hash. Chunk: stable chunk_id, policy_id/version, section_id, ordinal, text_hash, text, metadata. IngestionEvent: run_id, source_hash, operation, affected IDs, status, timestamps. Case: case_id, department/policy scope, customer scenario, graph thread ID. Decision: evidence/citations, policy versions, rationale, proposed action, approval status/actor/time. AuditEvent: trace ID, event type, actor, safe payload, timestamp.

## Ingestion contract
Discover → validate provenance/schema → normalize → hash → diff against catalog → transform changed documents only → preserve unchanged chunk IDs → upsert changed chunks → retire removed/superseded chunks → atomically advance version pointer → emit event/metrics. Failure must preserve the prior active index and permit idempotent retry/rollback.

## Retrieval/cache contract
Filter by selected departments and effective policy versions before ranking. Return chunks with stable citations, source/version/effective dates and score. Semantic cache keys include normalized query, department/policy-version scope, and retriever/embedding configuration; changes invalidate only affected scopes. Revalidate freshness before return.

## Graph and MCP contract
Typed graph state contains case, selected scope, retrieved evidence, bounded agent outputs, proposed action, approval result, trace ID. All tool calls cross MCP adapters. Human approval interrupts before any consequential action; resume validates actor and decision; no automatic refund/external write exists in demo mode. Cap graph iterations and retries.

## Test boundaries
Use a local temporary database/index and an in-process MCP test server for deterministic integration tests; these are real components, not mocks. Mock only the LLM/provider. See [evaluation plan](../evaluation/README.md).
