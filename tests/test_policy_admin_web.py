import json
from datetime import UTC, datetime
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


def make_client(tmp_path: Path, subject_id: str = "alice.chen"):
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
        clock=lambda: datetime(2026, 6, 2, tzinfo=UTC),
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
        json={"subject_id": subject_id},
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


def test_evidence_explorer_page_is_authenticated_and_has_search_controls(tmp_path):
    client, _ = make_client(tmp_path, subject_id="dana.ross")
    page = client.get("/evidence")
    assert page.status_code == 200
    assert "Evidence Explorer" in page.text
    assert "Find policy evidence" in page.text
    assert "department_id" in page.text
    assert "citation" in page.text
    assert "credentials:'same-origin'" in page.text
    assert "Evidence-only prototype" in page.text
    assert "Contoso Logistics" in page.text
    assert "Dana Ross" in page.text


def test_evidence_explorer_requires_identity(tmp_path):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    write_policy(source_dir, "billing-refunds", "1.0", "synthetic fixture")
    database = tmp_path / "unauth.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(occurred_at="2026-06-01T00:00:00Z")
    client = TestClient(create_app(environment="development", policy_service=PolicyChangeService(source_dir, catalog, index)), client=("127.0.0.1", 8000))
    assert client.get("/evidence").status_code == 401


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

def test_development_exposes_evidence_without_enabling_policy_lab(tmp_path, monkeypatch):
    import re
    from pathlib import Path

    from app.iam_store import SQLiteIAMStore
    from app.web_runtime import create_app as create_runtime_app

    monkeypatch.chdir(Path(__file__).resolve().parents[1])

    monkeypatch.delenv("REFUNDFLOW_POLICY_LAB", raising=False)
    monkeypatch.setenv("REFUNDFLOW_DATA_DIR", str(tmp_path))
    client = TestClient(create_runtime_app(environment="development"), client=("127.0.0.1", 8000), follow_redirects=False)
    homepage = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text).group(1)
    client.post("/dev/login", json={"subject_id": "bob.patel"}, headers={"x-csrf-token": csrf, "origin": "http://testserver"})

    dashboard = client.get("/")
    assert "authorization and document access are not implemented yet" not in dashboard.text.lower()
    assert client.get("/evidence").status_code == 200
    params = {"department_id": "billing", "query": "refund receipt", "as_of": "2026-11-22"}
    search = client.get("/api/policy-search", params=params)
    assert search.status_code == 200 and search.json()
    assert client.get("/policies").status_code == 200
    assert "Policy Change Lab" in client.get("/policies").text

    iam = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    # Revoke every billing-document grant bob holds; the department search must
    # then fail closed with no authorized evidence.
    for document in [
        item
        for item in iam.authorization_document_snapshot()[1]
        if item.tenant_id == "northstar-retail" and item.scope == "department:billing"
    ]:
        iam.put_document(document.model_copy(update={"grants": ()}), occurred_at="test-revoke")
    assert client.get("/api/policy-search", params=params).json() == []



def test_document_write_permission_requires_explicit_seed_and_authorizes_write(tmp_path):
    from app.iam import (
        AccessRequest,
        CasbinAuthorizationAdapter,
        Document,
        DocumentGrant,
        Membership,
        Principal,
        Role,
    )
    from app.iam_store import SQLiteIAMStore

    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_group("northstar-retail", "billing-analysts")
    principal = Principal(user_id="bob.patel", memberships=(Membership(tenant_id="northstar-retail", group_ids=("billing-analysts",)),))
    store.put_principal(principal)
    document = Document(id="billing-refunds", tenant_id="northstar-retail", owner_user_id="bob.patel", category="billing-policy", scope="department:billing", grants=(DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"), DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.write")))
    store.put_document(document)
    role = Role(id="unused", permissions=())
    write_only = document.model_copy(update={"grants": (DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.write"),)})
    adapter = CasbinAuthorizationAdapter((role,), principals=(principal,), documents=(write_only,))
    write_access = AccessRequest(action="document.write", scope=document.scope, category=document.category)
    assert adapter.authorize(principal, write_only, write_access)
    assert not adapter.authorize(principal, write_only, AccessRequest(action="document.read", scope=document.scope, category=document.category))
    assert not adapter.authorize(principal, write_only, AccessRequest(action="document.delete", scope=document.scope, category=document.category))


def test_ingestion_status_requires_current_tenant_membership(tmp_path, monkeypatch):
    import re

    from app.iam import Membership, Principal
    from app.iam_store import SQLiteIAMStore
    from app.policy_catalog import PolicyCatalog
    from app.web_runtime import create_app

    monkeypatch.setenv("REFUNDFLOW_POLICY_LAB", "1")
    monkeypatch.setenv("REFUNDFLOW_DATA_DIR", str(tmp_path))
    client = TestClient(
        create_app(environment="development"),
        client=("127.0.0.1", 8000),
        follow_redirects=False,
    )
    home = client.get("/")
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text)
    assert match is not None
    client.post(
        "/dev/login",
        json={"subject_id": "bob.patel"},
        headers={"x-csrf-token": match.group(1), "origin": "http://testserver"},
    )

    store = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    PolicyCatalog(tmp_path / "refundflow.sqlite3").record_file_status(
        "northstar-retail", "billing-refunds", status="indexed", message="ready", occurred_at="test"
    )
    assert client.get("/api/policy-ingestion-status").json()

    store.put_principal(
        Principal(
            user_id="bob.patel",
            memberships=(Membership(tenant_id="contoso-logistics", group_ids=("risk-reviewers",)),),
        ),
        occurred_at="test-revoke-membership",
    )
    assert client.get("/api/policy-ingestion-status").json() == []


def test_runtime_pdf_upload_authorization_and_page_citation(tmp_path, monkeypatch):
    import re

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from app.iam_store import SQLiteIAMStore
    from app.web_runtime import create_app

    monkeypatch.setenv("REFUNDFLOW_POLICY_LAB", "1")
    monkeypatch.setenv("REFUNDFLOW_DATA_DIR", str(tmp_path))
    client = TestClient(create_app(environment="development"), client=("127.0.0.1", 8000), follow_redirects=False)
    home = client.get("/"); csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text).group(1)
    client.post("/dev/login", json={"subject_id":"bob.patel"}, headers={"x-csrf-token":csrf,"origin":"http://testserver"})
    home = client.get("/"); csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text).group(1)
    writer=PdfWriter(); page=writer.add_blank_page(width=300,height=300)
    stream=DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 20 200 Td (newuploadcitation) Tj ET")
    page[NameObject("/Contents")]=writer._add_object(stream)
    font=DictionaryObject(); font[NameObject("/Type")]=NameObject("/Font"); font[NameObject("/Subtype")]=NameObject("/Type1"); font[NameObject("/BaseFont")]=NameObject("/Helvetica")
    fonts=DictionaryObject(); fonts[NameObject("/F1")]=writer._add_object(font); resources=DictionaryObject(); resources[NameObject("/Font")]=fonts; page[NameObject("/Resources")]=resources
    import io
    buffer=io.BytesIO(); writer.write(buffer); pdf=buffer.getvalue()
    headers={"origin":"http://testserver","x-csrf-token":csrf,"content-type":"application/pdf"}
    url="/api/policies/billing-refunds/pdf?new_version=9.9&effective_from=2026-11-19"
    store=SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    _, documents=store.authorization_document_snapshot()
    billing=next(item for item in documents if item.id=="billing-refunds")
    store.put_document(billing.model_copy(update={"grants":tuple(grant for grant in billing.grants if grant.permission!="document.write")}),occurred_at="test-revoke-write")
    revoked=client.post(url,content=b"not a pdf",headers=headers)
    assert revoked.status_code==403
    store.put_document(billing,occurred_at="test-restore-write")
    denied=TestClient(create_app(environment="development"),client=("127.0.0.1",8000),follow_redirects=False)
    denied_home=denied.get("/"); denied_csrf=re.search(r'<meta name="csrf-token" content="([^"]+)"',denied_home.text).group(1)
    denied.post("/dev/login",json={"subject_id":"dana.ross"},headers={"x-csrf-token":denied_csrf,"origin":"http://testserver"})
    assert denied.post(url,content=pdf,headers={**headers,"x-csrf-token":denied_csrf}).status_code==403
    assert client.post(url,content=pdf,headers={**headers,"origin":"https://attacker.example"}).status_code==403
    bad=client.post("/api/policies/billing-refunds/pdf?new_version=10.0&effective_from=2026-11-19",content=b"not a pdf",headers=headers)
    assert bad.status_code==422
    assert client.get("/api/policy-search",params={"policy_id":"billing-refunds","query":"refund receipt","as_of":"2026-11-20"}).json()[0]["citation"].startswith("billing-refunds@1.0#")
    response=client.post(url,content=pdf,headers=headers)
    assert response.status_code==200,response.text
    active_hash=client.get("/api/policies").json()
    active_hash=next(item["content_hash"] for item in active_hash if item["policy_id"]=="billing-refunds")
    results=client.get("/api/policy-search",params={"policy_id":"billing-refunds","query":"newuploadcitation","as_of":"2026-11-20"})
    assert results.status_code==200 and results.json()[0]["citation"].startswith("billing-refunds@9.9#page-1")
    writer2=PdfWriter(); page2=writer2.add_blank_page(width=300,height=300)
    stream2=DecodedStreamObject(); stream2.set_data(b"BT /F1 12 Tf 20 200 Td (differentpdfcontent) Tj ET")
    page2[NameObject("/Contents")]=writer2._add_object(stream2)
    font2=DictionaryObject(); font2[NameObject("/Type")]=NameObject("/Font"); font2[NameObject("/Subtype")]=NameObject("/Type1"); font2[NameObject("/BaseFont")]=NameObject("/Helvetica")
    fonts2=DictionaryObject(); fonts2[NameObject("/F1")]=writer2._add_object(font2); resources2=DictionaryObject(); resources2[NameObject("/Font")]=fonts2; page2[NameObject("/Resources")]=resources2
    changed_buffer=io.BytesIO(); writer2.write(changed_buffer)
    changed=client.post("/api/policies/billing-refunds/pdf?new_version=10.1&effective_from=2026-11-20",content=changed_buffer.getvalue(),headers=headers)
    assert changed.status_code==200,changed.text
    changed_policy=next(item for item in client.get("/api/policies").json() if item["policy_id"]=="billing-refunds")
    assert changed_policy["content_hash"] != active_hash
    assert client.get("/api/policy-search",params={"policy_id":"billing-refunds","query":"differentpdfcontent","as_of":"2026-11-20"}).json()[0]["citation"].startswith("billing-refunds@10.1#page-1")
    status=client.get("/api/policy-ingestion-status")
    assert status.status_code==200 and status.json()


def test_streamed_pdf_body_over_declared_length_preserves_active_version(tmp_path, monkeypatch):
    import re

    from fastapi.testclient import TestClient

    from app.web_runtime import create_app

    monkeypatch.setenv("REFUNDFLOW_POLICY_LAB", "1")
    monkeypatch.setenv("REFUNDFLOW_DATA_DIR", str(tmp_path))
    client=TestClient(create_app(environment="development"),client=("127.0.0.1",8000),follow_redirects=False)
    home=client.get("/"); csrf=re.search(r'<meta name="csrf-token" content="([^"]+)"',home.text).group(1)
    client.post("/dev/login",json={"subject_id":"bob.patel"},headers={"x-csrf-token":csrf,"origin":"http://testserver"})
    home=client.get("/"); csrf=re.search(r'<meta name="csrf-token" content="([^"]+)"',home.text).group(1)
    headers={"origin":"http://testserver","x-csrf-token":csrf,"content-type":"application/pdf","content-length":"1"}
    response=client.post("/api/policies/billing-refunds/pdf?new_version=77.8&effective_from=2026-11-20",content=b"oversized body",headers=headers)
    assert response.status_code==413
    assert all(item["version"]!="77.8" for item in client.get("/api/policies").json())
    citations=client.get("/api/policy-search",params={"policy_id":"billing-refunds","query":"refund receipt","as_of":"2026-11-20"})
    assert citations.status_code==200 and citations.json()[0]["citation"].startswith("billing-refunds@1.0#")
    assert not list((tmp_path / ".staging").glob("pdf-*.pdf"))


def test_local_development_seed_grants_only_bobs_billing_evidence_and_revokes(tmp_path, monkeypatch):
    import re

    from app.iam_store import SQLiteIAMStore
    from app.web_runtime import create_app as create_runtime_app

    monkeypatch.setenv("REFUNDFLOW_POLICY_LAB", "1")
    monkeypatch.setenv("REFUNDFLOW_DATA_DIR", str(tmp_path))
    client = TestClient(create_runtime_app(environment="development"), client=("127.0.0.1", 8000), follow_redirects=False)
    homepage = client.get("/")
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', homepage.text)
    assert csrf_match is not None
    csrf = csrf_match.group(1)
    login = client.post("/dev/login", json={"subject_id": "bob.patel"}, headers={"x-csrf-token": csrf, "origin": "http://testserver"})
    assert login.status_code == 303, login.text
    params = {"department_id": "billing", "query": "refund receipt", "as_of": "2026-11-22"}
    allowed = client.get("/api/policy-search", params=params)
    assert allowed.status_code == 200
    assert allowed.json()
    assert any(item["text"] for item in allowed.json())
    from app.iam import (
        AccessRequest,
        CasbinAuthorizationAdapter,
        Document,
        DocumentGrant,
        Role,
    )
    snapshot=SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    _, principals=snapshot.authorization_principal_snapshot()
    _, documents=snapshot.authorization_document_snapshot()
    bob=next(item for item in principals if item.user_id=="bob.patel")
    roles=tuple(Role(id=role,permissions=snapshot.list_role_permissions("northstar-retail",role)) for role in snapshot.list_roles("northstar-retail"))
    other=Document(id="billing-other",tenant_id="northstar-retail",owner_user_id="bob.patel",category="billing-policy",scope="department:billing",grants=(DocumentGrant(subject_type="user",subject_id="bob.patel",permission="document.read"),))
    read_only_adapter=CasbinAuthorizationAdapter(roles,principals=principals,documents=(*documents,other))
    assert read_only_adapter.authorize(bob,other,AccessRequest(action="document.read",scope=other.scope,category=other.category))
    assert not read_only_adapter.authorize(bob,other,AccessRequest(action="document.write",scope=other.scope,category=other.category))

    client.post("/dev/logout", headers={"x-csrf-token": re.search(r'<meta name="csrf-token" content="([^"]+)"', client.get("/").text).group(1), "origin": "http://testserver"})
    fresh = client.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', fresh.text).group(1)
    client.post("/dev/login", json={"subject_id": "dana.ross"}, headers={"x-csrf-token": csrf, "origin": "http://testserver"})
    denied = client.get("/api/policy-search", params=params)
    assert denied.status_code == 200 and denied.json() == []

    iam = SQLiteIAMStore(tmp_path / "refundflow.sqlite3")
    documents = [
        item
        for item in iam.authorization_document_snapshot()[1]
        if item.tenant_id == "northstar-retail" and item.scope == "department:billing"
    ]
    assert documents, "bob must hold at least one billing-document grant to revoke"
    for document in documents:
        iam.put_document(document.model_copy(update={"grants": ()}), occurred_at="test-revocation")
    bob = TestClient(create_runtime_app(environment="development"), client=("127.0.0.1", 8000))
    home = bob.get("/")
    csrf = re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text).group(1)
    bob.post("/dev/login", json={"subject_id": "bob.patel"}, headers={"x-csrf-token": csrf, "origin": "http://testserver"})
    assert bob.get("/api/policy-search", params=params).json() == []
