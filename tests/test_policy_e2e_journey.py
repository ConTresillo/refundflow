"""End-to-end user journey: loopback identity -> authorized lexical retrieval -> cited evidence.

The first demonstrable user-visible flow of the RefundFlow prototype. It wires the
existing loopback-only dev identity simulator through tenant resolution,
PyCasbin deny-by-default authorization, and real local SQLite FTS5 lexical
retrieval, ending with cited policy evidence (policy_id@version#section) that is
re-validated against the live grant on every request.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.iam import Document, DocumentGrant
from app.iam_store import SQLiteIAMStore
from tests.test_policy_admin_web import make_client

_AS_OF = "2026-06-02"


def _search(client: TestClient, params: dict) -> list[dict]:
    response = client.get("/api/policy-search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_e2e_journey_selects_server_tenant_authorized_cited_evidence(tmp_path):
    # 1) Launch the loopback app and pick the seeded demo identity; the server
    #    resolves the tenant context (northstar-retail) for that subject.
    client, _ = make_client(tmp_path)
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None, "loopback page must carry an anti-CSRF token"
    login = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert login.status_code == 303
    me = client.get("/api/me")
    identity = me.json()
    assert identity["tenant_id"] == "northstar-retail"
    assert identity["subject_id"] == "alice.chen"

    # 2) Search before any grant is denied by default (no cross-tenant leak).
    assert _search(client, {"department_id": "billing", "query": "receipt", "as_of": _AS_OF}) == []

    # 3) Authorize one document grant inside the identity's tenant.
    store = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    store.put_document(
        Document(
            id="billing-refunds",
            tenant_id="northstar-retail",
            owner_user_id="alice.chen",
            category="billing-policy",
            scope="department:billing",
            grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),),
        )
    )

    # 4) Authorized lexical search returns real cited evidence.
    results = _search(client, {"department_id": "billing", "query": "receipt", "as_of": _AS_OF})
    assert results, "authorized search must return at least one cited evidence item"
    citation = results[0]["citation"]
    assert citation.startswith("billing-refunds@"), citation
    assert "#excerpt-" in citation, citation

    # 5) A grant owned by another tenant never reaches this tenant's results.
    store.put_document(
        Document(
            id="billing-refunds",
            tenant_id="contoso-logistics",
            owner_user_id="dana.ross",
            category="billing-policy",
            scope="department:billing",
            grants=(DocumentGrant(subject_type="user", subject_id="dana.ross", permission="document.read"),),
        )
    )
    assert _search(client, {"department_id": "billing", "query": "receipt", "as_of": _AS_OF}) == []

    # 6) Revocation is enforced on the same request (fail-closed, no stale hit).
    store.put_document(
        Document(
            id="billing-refunds",
            tenant_id="northstar-retail",
            owner_user_id="alice.chen",
            category="billing-policy",
            scope="department:billing",
            grants=(),
        )
    )
    assert _search(client, {"department_id": "billing", "query": "receipt", "as_of": _AS_OF}) == []
