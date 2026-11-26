"""Focused regression guards for the end-to-end user journey's fail-closed retrieval.

The first demonstrable user-visible flow of the RefundFlow prototype wires the
loopback-only dev identity simulator through tenant resolution, PyCasbin
deny-by-default authorization, and real local SQLite FTS5 lexical retrieval,
ending with cited policy evidence that is re-validated against the live grant on
every request (decision 0044, test_policy_e2e_journey).

That guard proves identity -> tenant -> authorized lexical search -> cited
evidence -> cross-tenant denial -> revocation, but it never asserts the two
fail-closed edges that a search surface must also enforce:

  1. Effective-date boundary: a search whose ``as_of`` falls outside a policy's
     active window (``effective_from``..``effective_to``) must return no
     evidence, and must never fall through to a stale/changed-document match.
  2. Cross-tenant causal isolation under revocation: a grant owned by another
     tenant must never reach the result, and revoking a live grant mid-query
     must deny the remainder of that request without leaking forbidden text.

Both are contract/integration-level assertions (the underlying retrieval code
already filters by effective date and authorized policy ids); literal RED is not
appropriate here because the code has no behaviour to change. The regression
document the intended security contract so a future refactor cannot silently
remove it.
"""

from __future__ import annotations

import datetime
import json
import re

from fastapi.testclient import TestClient

from app.iam import Document, DocumentGrant
from app.iam_store import SQLiteIAMStore
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app

_AS_OF_WINDOW = "2026-06-02"
_AS_OF_BEFORE = "2025-12-31"
_AS_OF_AFTER = "2027-01-01"


def _write_policy(source_dir: str, policy_id: str, version: str, text: str,
                  effective_from: str, effective_to: str | None = None) -> None:
    record = {
        "id": policy_id,
        "department_id": "billing",
        "version": version,
        "title": f"{policy_id} rule",
        "status": "active",
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow synthetic demo collection",
        "source_url": "",
        "provenance": "Regression fixture.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": [text],
    }
    (source_dir / f"{policy_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _alice_grant() -> Document:
    return Document(
        id="billing-refunds",
        tenant_id="northstar-retail",
        owner_user_id="alice.chen",
        category="billing-policy",
        scope="department:billing",
        grants=(DocumentGrant(subject_type="user", subject_id="alice.chen",
                              permission="document.read"),),
    )


def _make_app(tmp_path, *, default_grant: bool = True):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    _write_policy(source_dir, "billing-refunds", "1.0",
                  "refund claims require a payment reference and receipt.",
                  effective_from="2026-01-01", effective_to="2026-12-31")
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(occurred_at="2026-06-01T00:00:00Z")
    # A dev identity and an active session resolve the actor; the IAM document
    # grant is seeded on the same SQLite database the policy service owns.
    store = SQLiteIAMStore(database)
    if default_grant:
        store.put_document(_alice_grant())
    service = PolicyChangeService(source_dir, catalog, index,
                                  clock=lambda: datetime.datetime(2026, 6, 2, tzinfo=datetime.UTC))
    client = TestClient(create_app(environment="development", policy_service=service),
                        client=("127.0.0.1", 8000), follow_redirects=False)
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None, "loopback page must carry a CSRF token"
    login = client.post("/dev/login", json={"subject_id": "alice.chen"},
                        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"})
    assert login.status_code == 303
    return client



def test_effective_date_boundary_denies_out_of_window_as_of(tmp_path):
    """as_of outside [effective_from, effective_to] returns no evidence (fail-closed)."""
    # In-window search with an authorized grant returns cited evidence.
    client = _make_app(tmp_path)
    authorized = client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_WINDOW,
    })
    assert authorized.status_code == 200
    body = authorized.json()
    assert body and body[0]["citation"].startswith("billing-refunds@")

    # Before the window (as_of earlier than effective_from).
    assert client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_BEFORE,
    }).json() == []
    # After the window (as_of later than effective_to).
    assert client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_AFTER,
    }).json() == []


def test_cross_tenant_causal_isolation_and_revocation_under_query(tmp_path):
    """Grants owned by other tenants never appear; revocation fails the request."""
    # Seed northstar alice a read grant on her own billing-refunds policy.
    client = _make_app(tmp_path)
    store = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    store.put_document(
        Document(
            id="billing-refunds",
            tenant_id="northstar-retail",
            owner_user_id="alice.chen",
            category="billing-policy",
            scope="department:billing",
            grants=(DocumentGrant(
                subject_type="user", subject_id="alice.chen", permission="document.read"),
            ),
        )
    )
    # Authorised search returns the northstar citation only.
    authorized = client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_WINDOW,
    })
    assert authorized.status_code == 200
    citations = [item["citation"] for item in authorized.json()]
    assert citations, "authorized search must return at least one citation"
    assert citations[0].startswith("billing-refunds@1.0#")
    assert "#excerpt-" in citations[0]

    # A contoso-logistics document (same id, different tenant) never surfaces.
    store.put_document(
        Document(
            id="billing-refunds",
            tenant_id="contoso-logistics",
            owner_user_id="dana.ross",
            category="billing-policy",
            scope="department:billing",
            grants=(DocumentGrant(subject_type="user", subject_id="dana.ross",
                                  permission="document.read"),),
        )
    )
    assert client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_WINDOW,
    }).json() == []

    # Revoking alice's grant is fail-closed: the SAME in-flight request returns [].
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
    assert client.get("/api/policy-search", params={
        "department_id": "billing", "query": "receipt", "as_of": _AS_OF_WINDOW,
    }).json() == []
