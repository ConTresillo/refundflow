# Observability — spec (P7b)

One run/trace ID is propagated across UI/API, graph, MCP call, retrieval,
ingestion, cache, approval, and evaluation events. Every component hook emits a
typed event with a stable envelope so logs, run reports, and retrospectives carry
the same tracing data without leaking secrets or unnecessary customer text/PII.

## Correlation context

A `CorrelationContext` is threaded through the call stack (thread-local so
concurrent hooks do not clobber each other):

- `correlation_id` — UUID4 (generated per request/workflow start; used as the
  top-level trace/run ID exposed to the UI/API).
- `actor_subject_id` — the server-resolved identity subject (never a
  client-supplied value).
- `tenant_id`, `document_id`, `thread_id` — server-resolved tenant/document/thread scope.
- `start_mono` — monotonic start time; `elapsed_ms()` derives duration.

## Event envelopes

Every event is a plain dict with a stable shape:

```json
{
  "correlation_id": "...",
  "component": "policy.retrieval",
  "event_type": "retrieve.start",
  "tenant_id": "northstar",
  "actor_subject_id": "user-123",
  "occurred_at": "2026-11-24T12:00:00Z",
  "duration_ms": 12.4,
  "payload": { "query": "...", "tenant_id": "northstar", "department_id": "billing" }
}
```

- Payload values are validated against an allow-list before logging/storage.
- `SAFE_KEYS` allow-list (identity, policy, document, run, duration/counter,
  result fields only). Anything not on the list is dropped.
- Values whose keys match `SECRET_KEY_HINTS` (`password`, `secret`, `token`,
  `api_key`, `bearer`, `private_key`) are rejected/raise if they leak into a
  payload.

## Component hooks

- `observe(component, stage, priority)` — context manager that starts the
  correlation timer and emits `<component>.<stage>.start` /
  `<component>.<stage>.end`.
- `record_event(component, event_type, payload)` — append-only event record with
  current correlation context; fail-closed if the context is missing.
- `EventSink` — no-op sink by default; swap for file/OpenTelemetry/console in
  production. The same module is provider-agnostic.

## Run report

A local run report (JSON) exposes: ingestion freshness, active policy versions,
graph trace, MCP tool activity, cache hit/miss/stale prevention, and evaluation
metrics. It never contains raw text, tokens, or customer PII.

## Retention and redaction

- Retain an append-only audit trail for simulation decisions; define
  retention/redaction policy before production.
- Structured logs, run reports, and events redact customer text, secrets, and
  cross-tenant identity. Authorization revocations, queue redelivery, provider
  timeouts, and audit failures are recorded as structured error categories
  (never raw customer/document text).
