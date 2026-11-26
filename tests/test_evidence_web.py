"""C1: freshness-aware retrieval E2E through the loopback Evidence Explorer UI.

Verifies that the live HTTP surface discloses real freshness metadata
(freshness_state, cache_status, source_age_seconds, authorization and
document revisions) and that the critical mode is always uncached.

These are web-level (loopback TestClient) checks over the real local
SQLite FTS5 lexical retriever; no provider, embedding or network dependency.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.iam import Document, DocumentGrant
from app.iam_store import SQLiteIAMStore
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app

_AS_OF = "2026-11-20"


def _make_client(tmp_path: Path, subject_id: str = "alice.chen"):
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
        "provenance": "RefundFlow synthetic demo collection.",
        "license_notes": "Synthetic demo.",
        "excerpts": ["Refund claims require a payment reference and receipt."],
    }
    (source_dir / "billing-refunds.json").write_text(
        __import__("json").dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(
        occurred_at="2026-06-01T00:00:00Z"
    )
    service = PolicyChangeService(
        source_dir,
        catalog,
        index,
        clock=lambda: datetime(2026, 6, 2, tzinfo=UTC),
    )
    client = TestClient(
        create_app(environment="development", policy_service=service),
        client=("127.0.0.1", 8000),
        follow_redirects=False,
    )
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text).group(1)
    login = client.post(
        "/dev/login",
        json={"subject_id": subject_id},
        headers={"x-csrf-token": csrf, "origin": "http://testserver"},
    )
    assert login.status_code == 303, login.text
    return client, service


def _authorize_subject(client: TestClient, subject_id: str, iam=None):
    """Seed a least-privilege document.read grant so the subject can search."""
    service = client.app.state.policy_service
    if iam is None:
        iam = SQLiteIAMStore(service._database)
    iam.put_document(
        Document(
            id="billing-refunds",
            tenant_id="northstar-retail",
            owner_user_id=subject_id,
            category="billing-policy",
            scope="department:billing",
            grants=(
                DocumentGrant(
                    subject_type="user", subject_id=subject_id, permission="document.read"
                ),
            ),
        )
    )


def _search(
    client: TestClient, *, query: str, as_of: str = _AS_OF, **params
) -> dict[str, object]:
    params.setdefault("department_id", "billing")
    params.setdefault("query", query)
    params.setdefault("as_of", as_of)
    response = client.get("/api/policy-search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Web UI (Evidence Explorer) control surface
# ---------------------------------------------------------------------------

def test_evidence_explorer_page_exposes_freshness_controls(tmp_path):
    client, _ = _make_client(tmp_path)
    page = client.get("/evidence")
    assert page.status_code == 200
    assert 'id="freshness"' in page.text
    assert 'id="fresh-rag"' in page.text
    assert 'evidence_mode: mode' in page.text


def test_evidence_page_requires_identity(tmp_path):
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
        "provenance": "synthetic",
        "license_notes": "demo",
        "excerpts": ["Refund claims require a payment reference and receipt."],
    }
    (source_dir / "billing-refunds.json").write_text(
        __import__("json").dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "unauth.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(
        occurred_at="2026-06-01T00:00:00Z"
    )
    client = TestClient(
        create_app(
            environment="development",
            policy_service=PolicyChangeService(
                source_dir, catalog, index, clock=lambda: datetime(2026, 6, 2, tzinfo=UTC)
            ),
        ),
        client=("127.0.0.1", 8000),
    )
    assert client.get("/evidence").status_code == 401


# ---------------------------------------------------------------------------
# Freshness disclosure in the search response
# ---------------------------------------------------------------------------

def test_search_response_discloses_real_freshness_metadata(tmp_path):
    client, _ = _make_client(tmp_path)
    _authorize_subject(client, "alice.chen")
    body = _search(client, query="refund receipt")
    assert body, "authorized search must return cited evidence"
    item = body[0]
    for field in (
        "policy_id",
        "version",
        "section_id",
        "citation",
        "freshness_state",
        "cache_status",
        "source_age_seconds",
        "authorization_revision",
        "document_revision",
    ):
        assert field in item, f"missing freshness disclosure field {field}"
    assert item["freshness_state"] in {"fresh", "freshness_pending", "unknown", "critical"}
    assert item["cache_status"] in {"miss", "off"}
    assert isinstance(item["source_age_seconds"], int)
    assert isinstance(item["authorization_revision"], int)
    assert isinstance(item["document_revision"], int)


def test_ttl_snapshot_cache_cleared_by_catalog_change(tmp_path):
    client, service = _make_client(tmp_path)
    _authorize_subject(client, "alice.chen")
    retriever = service._retriever

    first = _search(client, query="refund receipt", evidence_mode="ttl_snapshot")
    assert first[0]["cache_status"] == "miss"
    assert retriever._last_cache_status == "miss"

    second = _search(client, query="refund receipt", evidence_mode="ttl_snapshot")
    assert second[0]["cache_status"] == "exact_hit"
    assert retriever._last_cache_status == "exact_hit"

    # A content change bumps the catalog snapshot fingerprint and must clear the
    # envelope on the next request.
    from app.policy_ingestion import PolicyIngestionService

    record = {
        "id": "billing-refunds",
        "department_id": "billing",
        "version": "1.1",
        "title": "Refund redemptions",
        "status": "active",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow",
        "source_url": "",
        "provenance": "synthetic",
        "license_notes": "demo",
        "excerpts": [
            "Refund claims require a payment reference, receipt, and order id."
        ],
    }
    (tmp_path / "policies" / "billing-refunds.json").write_text(
        __import__("json").dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    PolicyIngestionService(tmp_path / "policies", service._catalog, service._index).sync(
        occurred_at="2026-06-02T00:00:00Z"
    )
    third = _search(client, query="refund receipt", evidence_mode="ttl_snapshot")
    assert third[0]["cache_status"] == "miss"
    assert retriever._last_cache_status == "miss"


# ---------------------------------------------------------------------------
# Critical mode: always uncached
# ---------------------------------------------------------------------------

def test_critical_mode_always_uncached(tmp_path):
    client, _ = _make_client(tmp_path)
    _authorize_subject(client, "alice.chen")
    first = _search(client, query="refund receipt", evidence_mode="critical")
    second = _search(client, query="refund receipt", evidence_mode="critical")
    assert first[0]["cache_status"] == "bypassed"
    assert second[0]["cache_status"] == "bypassed"
    # Critical path must never reuse a cached envelope even on repeat requests.
    assert first is not second


def test_critical_mode_fails_closed_when_subject_has_no_grant(tmp_path):
    client, _ = _make_client(tmp_path)
    # No grant is seeded for alice.chen.
    body = _search(client, query="refund receipt", evidence_mode="critical")
    assert body == []
