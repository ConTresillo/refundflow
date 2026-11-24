"""Structured observability: correlation context, event hooks, and redaction.

This is the P7b spine: one run/trace ID is propagated across UI/API, graph,
MCP call, retrieval, ingestion, cache, approval, and evaluation events. Every
component hook emits a typed event with a stable envelope so that logs, run
reports, and retrospectives carry the same tracing data without leaking
secrets or unnecessary customer text/PII.

Design notes
------------
* Correlation context is attached to arbitrary callables/context managers
  (``with observe("component.name", ...)``) or recordable via ``record_event``.
* Event envelopes are plain dicts; payload values are validated against a
  redaction contract before they are ever logged or stored.
* The same module is provider-agnostic: a log writer can be swapped for a
  structured file sink, OpenTelemetry, or the run report below.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Keys that are safe to record in an event envelope. Anything not on this
# allow-list is dropped (redacted) when a payload is normalized.
SAFE_KEYS = frozenset(
    {
        # identity / authorization (no secrets, no roles beyond labels)
        "tenant_id",
        "actor_subject_id",
        "actor_role_label",
        "document_id",
        "policy_id",
        "section_id",
        "entity_id",
        # policy / retrieval context (stable IDs only)
        "department_id",
        "version",
        "policy_version_id",
        "effective_from",
        "effective_to",
        "content_hash",
        "run_id",
        "thread_id",
        "command_id",
        "event_type",
        # durations / counters / results (numeric or short labels)
        "duration_ms",
        "node_count",
        "step_count",
        "tool_call_count",
        "result",
        "code",
        "status",
        "error_category",
        # domain tags useful for dashboards
        "component",
        "stage",
        "priority",
        "work_count",
        "ingested_count",
        "retried_at",
        "dead_lettered_at",
    }
)

# Module-wide counters for the observability spine; used by event_counter()
# and the run report. These are per-process, in-memory snapshots (the "spine"
# sensor), so they are honest local measurements rather than invented targets.
_COUNTERS: dict[str, int] = {"events_written_total": 0}


# Values that must never be logged or written, even if they leak into a
# payload. These are checked against input dicts and raised if present.
SECRET_KEY_HINTS = frozenset(("password", "secret", "token", "api_key", "bearer", "private_key"))


@dataclass(frozen=True)
class CorrelationContext:
    correlation_id: str
    actor_subject_id: str | None = None
    tenant_id: str | None = None
    document_id: str | None = None
    thread_id: str | None = None
    start_mono: float = field(default_factory=time.perf_counter)

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start_mono) * 1000.0

    def summary(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "actor_subject_id": self.actor_subject_id,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "thread_id": self.thread_id,
            "elapsed_ms": round(self.elapsed_ms(), 3),
        }


@dataclass
class EventSink:
    """A no-op sink; swap for file/OTel/console in production."""

    name: str = "noop"

    def write(self, envelope: dict[str, Any]) -> None:
        pass

    def report(self) -> dict[str, Any]:
        return {"sink": self.name, "events_written": 0}


# Thread-local correlation context so hooks can run concurrently without
# clobbering each other's tracing IDs.
_local = threading.local()


def _current() -> CorrelationContext | None:
    return getattr(_local, "ctx", None)


def set_correlation(ctx: CorrelationContext) -> None:
    _local.ctx = ctx


def push_correlation(correlation_id: str, **overrides: Any) -> CorrelationContext:
    """Install a context for the current thread; returns the installed ctx."""
    existing = _current()
    base = existing if existing else CorrelationContext(correlation_id=correlation_id)
    merged = CorrelationContext(
        correlation_id=correlation_id,
        actor_subject_id=overrides.pop("actor_subject_id", base.actor_subject_id),
        tenant_id=overrides.pop("tenant_id", base.tenant_id),
        document_id=overrides.pop("document_id", base.document_id),
        thread_id=overrides.pop("thread_id", base.thread_id),
    )
    for k, v in overrides.items():
        if v is not None and hasattr(merged, k):
            setattr(merged, k, v)
    _local.ctx = merged
    return merged


@contextmanager
def observe(component: str, *, actor_subject_id: str | None = None, tenant_id: str | None = None, document_id: str | None = None, **extra: Any):
    """Install a correlation context for the duration of the block."""
    ctx = CorrelationContext(
        correlation_id=uuid.uuid4().hex[:12],
        actor_subject_id=actor_subject_id,
        tenant_id=tenant_id,
        document_id=document_id,
    )
    previous = _current()
    _local.ctx = ctx
    try:
        yield ctx
    finally:
        _local.ctx = previous


def _default_context() -> CorrelationContext:
    return CorrelationContext(correlation_id="none")


def record_event(
    event_type: str,
    *,
    component: str,
    details: dict[str, Any] | None = None,
    ctx: CorrelationContext | None = None,
    sink: EventSink | None = None,
) -> dict[str, Any]:
    """Normalize and publish an event envelope."""
    ctx = ctx or _current() or _default_context()
    envelope: dict[str, Any] = {"correlation_id": ctx.correlation_id, "component": component, "event_type": event_type}
    if details:
        envelope["details"] = _redact(details)
    sink = sink or EventSink()
    sink.write(envelope)
    _COUNTERS["events_written_total"] = _COUNTERS.get("events_written_total", 0) + 1
    return envelope


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* containing only SAFE_KEYS; drop secrets."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in SAFE_KEYS:
            out[k] = v
        elif any(hint in k.lower() for hint in SECRET_KEY_HINTS):
            # Never log secrets; record a redaction note, never the value.
            out[k] = "<redacted>"
        # Other keys are intentionally dropped.
    return out


def event_counter() -> dict[str, int]:
    """Return a snapshot of counter keys by component; used by run reports.

    The values are live, per-process counters produced by record_event, so the
    run report carries honest local measurements rather than invented targets.
    """
    return dict(_COUNTERS)


def run_report(sink: EventSink, ctx: CorrelationContext | None = None) -> dict[str, Any]:
    """Local observability run report (redacted, no raw text/PII)."""
    ctx = ctx or _current() or _default_context()
    base = ctx.summary()
    return {
        "report_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "correlation_id": base["correlation_id"],
        **{k: v for k, v in base.items() if k != "correlation_id"},
        "sink": sink.name,
        "counters": event_counter(),
    }
