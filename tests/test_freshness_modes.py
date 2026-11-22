import datetime
import json

import pytest
from fastapi.testclient import TestClient

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.retrieval import SQLiteEvidenceRetriever
from app.web import create_app


def _make_service(tmp_path, subject_id="alice.chen", evidence_mode="freshness_preferred"):
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
        "source_name": "RefundFlow synthetic policy collection",
        "source_url": "",
        "provenance": "RefundFlow synthetic demo collection.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": ["Refund claims require a payment reference and receipt."],
    }
    (source_dir / "billing-refunds.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    ingestion = PolicyIngestionService(source_dir, catalog, index)
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    service = PolicyChangeService(
        source_dir, catalog, index, clock=lambda: datetime.datetime(2026, 6, 2, tzinfo=datetime.timezone.utc)
    )
    client = TestClient(create_app(environment="development", policy_service=service), client=("127.0.0.1", 8000), follow_redirects=False)
    homepage = client.get("/")
    import re

    csrf = re.search(r'<meta name="csrf-token" content="([^\"]+)"', homepage.text)
    assert csrf is not None
    login = client.post(
        "/dev/login",
        json={"subject_id": subject_id},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert login.status_code == 303
    return client, service


def _api(client, **params):
    params.setdefault("as_of", "2026-11-20")
    return client.get("/api/policy-search", params=params)


def _login_and_get(client, department, query, **extra):
    params = {
        "department_id": department,
        "query": query,
        "as_of": "2026-11-20",
        **extra,
    }
    return client.get("/api/policy-search", params=params)


def test_search_returns_freshness_disclosure_in_backward_compatible_shape(tmp_path):
    client, _service = _make_service(tmp_path)
    response = _api(client, department_id="billing", query="refund receipt")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    if body:
        item = body[0]
        assert "freshness_state" in item
        assert "cache_status" in item
        assert "source_age_seconds" in item
        assert "authorization_revision" in item
        assert "document_revision" in item
        assert "section_id" in item
        assert item["citation"].startswith("billing-refunds@")


def test_critical_mode_always_uncached_and_authorizes_each_page(tmp_path):
    client, _service = _make_service(tmp_path)
    first = _api(client, department_id="billing", query="refund receipt", evidence_mode="critical")
    second = _api(client, department_id="billing", query="refund receipt", evidence_mode="critical")
    assert first.status_code == 200
    assert second.status_code == 200
    first_body, second_body = first.json(), second.json()
    if first_body and second_body:
        assert first_body[0]["cache_status"] == "bypassed"
        assert second_body[0]["cache_status"] == "bypassed"


def test_ttl_snapshot_cache_invalidation_on_catalog_change(tmp_path):
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
        "provenance": "demo",
        "license_notes": "synthetic",
        "excerpts": ["Refund claims require a payment reference and receipt."],
    }
    (source_dir / "billing-refunds.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    database = tmp_path / "db.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(occurred_at="2026-06-01T00:00:00Z")
    retriever = SQLiteEvidenceRetriever(
        index,
        catalog,
        evidence_mode="ttl_snapshot",
        answer_cache="on",
        cache_ttl_seconds=60,
    )
    first = retriever.retrieve(
        "refund receipt",
        evidence_mode="ttl_snapshot",
        answer_cache="exact",
        departments={"billing"},
        as_of="2026-11-20",
        allowed_policy_ids={"billing-refunds"},
        top_k=5,
    )
    assert first
    assert retriever._last_cache_status == "miss"
    second = retriever.retrieve(
        "refund receipt",
        evidence_mode="ttl_snapshot",
        answer_cache="exact",
        departments={"billing"},
        as_of="2026-11-20",
        allowed_policy_ids={"billing-refunds"},
        top_k=5,
    )
    assert second
    assert retriever._last_cache_status == "exact_hit"

    # A catalog change moves the snapshot fingerprint and must invalidate the envelope.
    # Content changes require an immutable version bump for the source sync to apply.
    record["version"] = "1.1"
    record["excerpts"] = ["Refund claims require a payment reference, receipt, and order id."]
    (source_dir / "billing-refunds.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    PolicyIngestionService(source_dir, catalog, index).sync(occurred_at="2026-06-02T00:00:00Z")
    third = retriever.retrieve(
        "refund receipt",
        evidence_mode="ttl_snapshot",
        answer_cache="exact",
        departments={"billing"},
        as_of="2026-11-20",
        allowed_policy_ids={"billing-refunds"},
        top_k=5,
    )
    assert third
    assert retriever._last_cache_status == "miss"


def test_unknown_evidence_mode_is_rejected():
    from app.incremental_index import SQLitePolicyIndex

    index = SQLitePolicyIndex(":memory:")
    catalog = PolicyCatalog(":memory:")
    with pytest.raises(ValueError):
        SQLiteEvidenceRetriever(
            index, catalog, evidence_mode="bogus", answer_cache="off"
        ).retrieve("x", departments={"billing"}, as_of="2026-11-20", top_k=1)


def test_evidence_page_exposes_freshness_controls(tmp_path):
    client, _ = _make_service(tmp_path)
    page = client.get("/evidence")
    assert page.status_code == 200
    assert 'id="freshness"' in page.text
    assert 'id="fresh-rag"' in page.text
    assert 'evidence_mode: mode' in page.text
