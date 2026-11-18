import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app


def write_policy(directory: Path, policy_id: str, version: str, text: str) -> None:
    record = {
        "id": policy_id,
        "department_id": "billing",
        "version": version,
        "title": f"{policy_id} rules",
        "status": "active",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow test policies",
        "source_url": "",
        "provenance": "Offline test fixture.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": [text],
    }
    (directory / f"{policy_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_client(tmp_path: Path):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    write_policy(
        source_dir, "billing-refunds", "1.0", "legacyclaim needs an order receipt."
    )
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    ingestion = PolicyIngestionService(source_dir, catalog, index)
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    policy_service = PolicyChangeService(
        source_dir,
        catalog,
        index,
        clock=lambda: datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    client = TestClient(
        create_app(environment="development", policy_service=policy_service),
        client=("127.0.0.1", 8000),
        follow_redirects=False,
    )
    homepage = client.get("/")
    import re

    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf is not None
    login = client.post(
        "/dev/login",
        json={"subject_id": "alice.chen"},
        headers={"x-csrf-token": csrf.group(1), "origin": "http://testserver"},
    )
    assert login.status_code == 303
    dashboard = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', dashboard.text)
    assert csrf is not None
    headers = {"x-csrf-token": csrf.group(1), "origin": "http://testserver"}
    return client, headers


def test_policy_search_filters_acl_before_top_k_ranking_and_rechecks_revocation(tmp_path):
    from app.iam import Document, DocumentGrant
    from app.iam_store import SQLiteIAMStore

    client, _ = make_client(tmp_path)
    source_dir = tmp_path / "policies"
    write_policy(source_dir, "billing-private", "1.0", "common")
    index = SQLitePolicyIndex(tmp_path / "refundflow.sqlite3")
    index.ingest([
        {"policy_id": "billing-refunds", "version": "1.0", "department_id": "billing", "section_id": "rules", "text": "common"},
        {"policy_id": "billing-private", "version": "1.0", "department_id": "billing", "section_id": "rules", "text": "common common common common common"},
    ])
    iam = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    # Seed the allowed grant before querying; the rival is explicitly denied.
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),)))
    iam.put_document(Document(id="billing-private", tenant_id="northstar-retail", owner_user_id="bob.patel", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"),)))
    service = client.app.state.policy_service
    original_retrieve = service._retriever.retrieve

    def revoke_during_retrieval(*args, **kwargs):
        result = original_retrieve(*args, **kwargs)
        iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=()))
        return result

    service._retriever.retrieve = revoke_during_retrieval
    params = {"department_id": "billing", "query": "common", "as_of": "2026-06-02", "top_k": 1}
    raced = client.get("/api/policy-search", params=params)
    assert raced.status_code == 200
    assert raced.json() == []
    service._retriever.retrieve = original_retrieve
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),)))
    results = client.get("/api/policy-search", params=params).json()
    assert [item["policy_id"] for item in results] == ["billing-refunds"]
    assert all("common common common" not in item.get("text", "") for item in results)
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=()))
    assert client.get("/api/policy-search", params=params).json() == []


def test_policy_search_denies_until_document_grant_is_explicitly_seeded(tmp_path):
    from app.iam import Document, DocumentGrant
    from app.iam_store import SQLiteIAMStore

    client, _ = make_client(tmp_path)
    denied = client.get(
        "/api/policy-search",
        params={"policy_id": "billing-refunds", "query": "legacyclaim", "as_of": "2026-06-02"},
    )
    assert denied.status_code == 200
    assert denied.json() == []

    store = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    store.put_document(
        Document(
            id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen",
            category="billing-policy", scope="department:billing",
            grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),),
        )
    )
    allowed = client.get(
        "/api/policy-search",
        params={"policy_id": "billing-refunds", "query": "legacyclaim", "as_of": "2026-06-02"},
    )
    assert allowed.status_code == 200
    assert allowed.json()[0]["citation"].startswith("billing-refunds@1.0#")
    # A document owned only by another tenant cannot grant access to this ID.
    store.put_document(Document(id="billing-refunds", tenant_id="contoso-logistics", owner_user_id="dana.ross", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="dana.ross", permission="document.read"),)))
    assert client.get(
        "/api/policy-search",
        params={"policy_id": "billing-refunds", "query": "legacyclaim", "as_of": "2026-06-02"},
    ).json() == []



def test_policy_search_rechecks_live_grant_and_revocation(tmp_path):
    from app.iam import Document, DocumentGrant
    from app.iam_store import SQLiteIAMStore

    client, _ = make_client(tmp_path)
    params = {"policy_id": "billing-refunds", "query": "legacyclaim", "as_of": "2026-06-02"}
    assert client.get("/api/policy-search", params=params).json() == []
    iam = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="bob.patel", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"),)))
    assert client.get("/api/policy-search", params=params).json() == []
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),)))
    result = client.get("/api/policy-search", params=params).json()
    assert result[0]["citation"].startswith("billing-refunds@1.0#")
    iam.put_document(Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen", category="billing-policy", scope="department:billing", grants=()))
    assert client.get("/api/policy-search", params=params).json() == []

def test_policy_lab_requires_the_local_demo_identity(tmp_path):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    write_policy(
        source_dir, "billing-refunds", "1.0", "legacyclaim needs an order receipt."
    )
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(
        occurred_at="2026-06-01T00:00:00Z"
    )
    service = PolicyChangeService(source_dir, catalog, index)
    client = TestClient(
        create_app(environment="development", policy_service=service),
        client=("127.0.0.1", 8000),
    )

    assert client.get("/policies").status_code == 401
    assert client.get("/api/policies").status_code == 401


def test_policy_change_ui_api_review_publish_and_retrieval_are_end_to_end(tmp_path):
    client, headers = make_client(tmp_path)

    page = client.get("/policies")
    policies_response = client.get("/api/policies")
    assert page.status_code == 200
    assert "Policy Change Lab" in page.text
    assert "SQLite FTS" in page.text
    policies = policies_response.json()
    assert policies_response.status_code == 200
    assert policies[0]["version"] == "1.0"
    assert policies[0]["index_reflected"] is True

    draft_response = client.post(
        "/api/policy-drafts",
        headers=headers,
        json={
            "policy_id": "billing-refunds",
            "expected_version": "1.0",
            "expected_hash": policies[0]["content_hash"],
            "new_version": "2.0",
            "title": "Refund eligibility — revised",
            "effective_from": "2026-06-02",
            "effective_to": None,
            "excerpts": ["Refund claims require a payment reference and receipt."],
        },
    )
    assert draft_response.status_code == 201
    draft = draft_response.json()
    assert draft["state"] == "draft"
    assert "+Refund claims require a payment reference and receipt." in draft["diff"]

    missing_origin = client.post(
        f"/api/policy-drafts/{draft['draft_id']}/review",
        headers={"x-csrf-token": headers["x-csrf-token"]},
        json={"diff_hash": draft["diff_hash"]},
    )
    assert missing_origin.status_code == 403
    assert (
        client.get(f"/api/policy-drafts/{draft['draft_id']}").json()["state"] == "draft"
    )

    reviewed = client.post(
        f"/api/policy-drafts/{draft['draft_id']}/review",
        headers=headers,
        json={"diff_hash": draft["diff_hash"]},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["state"] == "reviewed"

    published = client.post(
        f"/api/policy-drafts/{draft['draft_id']}/publish", headers=headers
    )
    assert published.status_code == 200
    report = published.json()
    assert report["status"] == "succeeded"
    assert report["index_reflected"] is True
    assert report["new_version"] == "2.0"
    assert report["counts"]["changed"] == 1

    updated = client.get("/api/policies").json()[0]
    assert updated["version"] == "2.0"
    assert updated["index_reflected"] is True
    results = client.get(
        "/api/policy-search",
        params={"policy_id": "billing-refunds", "query": "payment reference", "as_of": "2026-06-02"},
    )
    assert results.status_code == 200
    assert results.json() == []
    assert client.get("/api/policy-change-runs").json()[0]["run_id"] == report["run_id"]


def test_policy_write_api_requires_csrf_and_rejects_cross_origin(tmp_path):
    client, headers = make_client(tmp_path)
    policies = client.get("/api/policies").json()
    payload = {
        "policy_id": "billing-refunds",
        "expected_version": "1.0",
        "expected_hash": policies[0]["content_hash"],
        "new_version": "2.0",
        "title": "Refund rules revised",
        "effective_from": "2026-06-02",
        "effective_to": None,
        "excerpts": ["Payment-reference proof is required."],
    }

    no_csrf = client.post(
        "/api/policy-drafts",
        headers={"origin": "http://testserver"},
        json=payload,
    )
    wrong_origin = client.post(
        "/api/policy-drafts",
        headers={**headers, "origin": "https://attacker.example"},
        json=payload,
    )
    assert no_csrf.status_code == 403
    assert wrong_origin.status_code == 403
    assert client.get("/api/policy-drafts").json() == []
