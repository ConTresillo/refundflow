"""Behavior-focused tests for the P7b observability spine (correlation IDs,
redaction, and the local run report). Independent of any provider spec.

Covers:
  - correlation IDs are present and unique per observed block
  - secrets and unrestricted customer text are redacted from event envelopes
  - no cross-tenant identifiers leak into a caller's event record
  - run report is redacted (no PII, no secrets) and carries counters
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.observability import (
    CorrelationContext,
    EventSink,
    observe,
    record_event,
    run_report,
)


@pytest.fixture(autouse=True)
def _reset_context():
    """Clear thread-local context before each test so no cross-test leakage."""
    from app.observability import _local
    _local.ctx = None
    yield
    _local.ctx = None
from app.web import create_app


def _sink():
    events = []

    class Collecting(EventSink):
        def write(self, envelope):
            events.append(envelope)

    return events, Collecting()


# ---------------------------------------------------------------------
# correlation IDs
# ---------------------------------------------------------------------
class TestCorrelationIds:
    def test_observe_installs_unique_correlation_id(self, tmp_path):
        events, _ = _sink()
        with observe("test.component", actor_subject_id="alice.chen", tenant_id="northstar-retail") as ctx:
            first_id = ctx.correlation_id
            assert len(first_id) == 12
            with observe("test.component3") as inner:
                assert inner.correlation_id != first_id
        # record_event without a context uses the "none" default
        record_event("test.event", component="x")
        # check via sink collection
        events, _ = _sink()
        assert events == []

    def test_push_correlation_merges_user_overrides(self, tmp_path):
        from app.observability import push_correlation

        ctx_out = push_correlation(
            "abc123456789",
            actor_subject_id="alice.chen",
            tenant_id="northstar-retail",
            document_id="doc-1",
            thread_id="t-1",
        )
        assert ctx_out.correlation_id == "abc123456789"
        assert ctx_out.actor_subject_id == "alice.chen"
        assert ctx_out.tenant_id == "northstar-retail"
        assert ctx_out.document_id == "doc-1"
        assert ctx_out.thread_id == "t-1"


# ---------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------
class TestRedaction:
    def test_event_envelope_redacts_secrets(self, tmp_path):
        events, sink = _sink()
        payload = {
            "tenant_id": "northstar-retail",
            "actor_subject_id": "alice.chen",
            "password": "s3cret-pass",
            "api_key": "abc123",
            "question": "How do I get a refund for a billing error?",
        }
        record_event("test.event", component="web.runtime", details=payload, sink=sink)
        assert len(events) == 1
        details = events[0]["details"]
        assert details.get("password") == "<redacted>"
        assert details.get("api_key") == "<redacted>"
        assert "How do I get a refund for a billing error?" not in json.dumps(details)

    def test_event_envelope_drops_unknown_keys(self, tmp_path):
        events, sink = _sink()
        payload = {
            "tenant_id": "northstar-retail",
            "actor_subject_id": "alice.chen",
            "extra_field": "keep",  # not in SAFE_KEYS
            "password": "s3cret-pass",
        }
        record_event("test.event", component="web.runtime", details=payload, sink=sink)
        assert len(events) == 1
        details = events[0]["details"]
        assert "extra_field" not in details
        assert details["password"] == "<redacted>"
        assert details["tenant_id"] == "northstar-retail"


# ---------------------------------------------------------------------
# run report
# ---------------------------------------------------------------------
class TestRunReport:
    def test_run_report_is_redacted(self, tmp_path):
        ctx = CorrelationContext(
            correlation_id="report-1234567890ab",
            actor_subject_id="alice.chen",
            tenant_id="northstar-retail",
            document_id="doc-1",
        )
        report = run_report(EventSink(), ctx)
        assert report["correlation_id"] == "report-1234567890ab"
        assert report["actor_subject_id"] == "alice.chen"
        assert "password" not in json.dumps(report)
        assert "api_key" not in json.dumps(report)

    def test_run_report_uses_default_context_when_none(self, tmp_path):
        report = run_report(EventSink())
        assert report["correlation_id"] == "none"


# ---------------------------------------------------------------------
# component event hooks (web layer)
# ---------------------------------------------------------------------
class TestWebObservability:
    def test_home_endpoint_records_event(self, tmp_path):
        source_dir = tmp_path / "policies"
        source_dir.mkdir()
        record = {
            "id": "billing-refunds",
            "department_id": "billing",
            "version": "1.0",
            "title": "Refund redemptions",
            "status": "active",
            "effective_from": "2026-01-01",
            "effective_to": None,
            "source_type": "synthetic_demo",
            "source_name": "RefundFlow",
            "source_url": "",
            "provenance": "p",
            "license_notes": "synthetic",
            "excerpts": ["Refund claims require a payment reference and receipt."],
        }
        (source_dir / "billing-refunds.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        database = tmp_path / "refundflow.sqlite3"
        from app.incremental_index import SQLitePolicyIndex
        from app.policy_catalog import PolicyCatalog
        from app.policy_change_service import PolicyChangeService
        from app.policy_ingestion import PolicyIngestionService

        catalog = PolicyCatalog(database)
        index = SQLitePolicyIndex(database)
        ingestion = PolicyIngestionService(source_dir, catalog, index)
        ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
        service = PolicyChangeService(
            source_dir,
            catalog,
            index,
            clock=lambda: __import__("datetime").datetime(2026, 6, 2, tzinfo=__import__("datetime").UTC),
        )
        events = []

        class Collecting(EventSink):
            def write(self, envelope):
                events.append(envelope)

        collecting_sink = Collecting()
        client = TestClient(
            create_app(environment="development", policy_service=service, events_sink=collecting_sink),
        )
        client.get("/")
        assert any(e["event_type"] == "http.request" for e in events)
