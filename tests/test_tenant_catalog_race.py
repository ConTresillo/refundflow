import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.iam import Document, DocumentGrant
from app.iam_store import SQLiteIAMStore
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app


def write_policy(directory: Path, policy_id: str, version: str, text: str) -> None:
    record = {
        "id": policy_id, "department_id": "billing", "version": version,
        "title": f"{policy_id} rules", "status": "active", "effective_from": "2026-01-01",
        "effective_to": None, "source_type": "synthetic_demo", "source_name": "test fixture",
        "source_url": "", "provenance": "Offline test fixture.",
        "license_notes": "Synthetic demo text.", "excerpts": [text],
    }
    (directory / f"{policy_id}.json").write_text(json.dumps(record), encoding="utf-8")


def make_client(tmp_path: Path):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    write_policy(source_dir, "billing-refunds", "1.0", "legacyclaim needs an order receipt.")
    database = tmp_path / "refundflow.sqlite3"
    catalog, index = PolicyCatalog(database), SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(occurred_at="2026-06-01T00:00:00Z")
    service = PolicyChangeService(source_dir, catalog, index, clock=lambda: datetime(2026, 6, 2, tzinfo=timezone.utc))
    client = TestClient(create_app(environment="development", policy_service=service), client=("127.0.0.1", 8000), follow_redirects=False)
    import re
    home = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text).group(1)
    login = client.post("/dev/login", json={"subject_id": "alice.chen"}, headers={"x-csrf-token": csrf, "origin": "http://testserver"})
    assert login.status_code == 303
    return client


def test_policy_search_denies_if_catalog_changes_during_fts_read(tmp_path):
    client = make_client(tmp_path)
    store = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    store.put_document(Document(
        id="billing-refunds", tenant_id="northstar-retail", owner_user_id="alice.chen",
        category="billing-policy", scope="department:billing",
        grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),),
    ))
    service = client.app.state.policy_service
    original_retrieve = service._retriever.retrieve

    def withdraw_during_retrieval(*args, **kwargs):
        result = original_retrieve(*args, **kwargs)
        service._catalog.withdraw("billing-refunds", occurred_at="2026-06-02T01:00:00Z", reason="race fixture")
        return result

    service._retriever.retrieve = withdraw_during_retrieval
    response = client.get("/api/policy-search", params={
        "policy_id": "billing-refunds", "query": "legacyclaim", "as_of": "2026-06-02",
    })
    assert response.status_code == 200
    assert response.json() == []


def test_authorization_parameters_are_an_all_or_none_search_contract(tmp_path):
    source = tmp_path / "policies"
    source.mkdir()
    write_policy(source, "billing-refunds", "1.0", "legacyclaim receipt")
    db = tmp_path / "db.sqlite3"
    catalog, index = PolicyCatalog(db), SQLitePolicyIndex(db)
    PolicyIngestionService(source, catalog, index).sync(occurred_at="2026-06-01T00:00:00Z")
    service = PolicyChangeService(source, catalog, index, clock=lambda: datetime(2026, 6, 2, tzinfo=timezone.utc))
    assert service.search("legacyclaim", policy_id="billing-refunds", as_of="2026-06-02", tenant_id="northstar-retail") == []
