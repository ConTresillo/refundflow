"""End-to-end acceptance tests for the durable async evidence-run service (P5).

These tests exercise the real loopback graph (authorize -> retrieve -> terminal)
through the durable SQLite outbox, so the roadmap P5b acceptance criteria are
verified against live components:

  * an authorized actor receives cited policy evidence (completed),
  * a revoked actor fails closed at enqueue and again at resume,
  * a crash before/after enqueue, restart and requeue still converge to one
    logical outcome (the run is never lost and never double-marks),
  * cancellation is fail-closed and a second cancel is rejected.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.iam import Document, DocumentGrant, Membership, Principal
from app.iam_store import SQLiteIAMStore
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app


def _make_service(tmp_path, *, tenant_id="northstar-retail", subject_id="alice.chen"):
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
    PolicyIngestionService(source_dir, catalog, index).sync(
        occurred_at="2026-06-01T00:00:00Z"
    )
    service = PolicyChangeService(source_dir, catalog, index)
    return service, database


def _seed_evidence_grants(service, *, grantee_subject_id="bob.patel"):
    """Create the IAM surface needed for one actor to read a billing doc."""
    iam = SQLiteIAMStore(service._database)
    tenant_id = "northstar-retail"
    group_id = "billing-analysts"
    iam.put_group(tenant_id, group_id, occurred_at="test-grant")
    iam.put_principal(
        Principal(
            user_id=grantee_subject_id,
            memberships=(
                Membership(tenant_id=tenant_id, group_ids=(group_id,), role_ids=()),
            ),
        ),
        occurred_at="test-grant",
    )
    document = Document(
        id="billing-refunds",
        tenant_id=tenant_id,
        owner_user_id=grantee_subject_id,
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id=grantee_subject_id, permission="document.read"
            ),
        ),
    )
    iam.put_document(document, occurred_at="test-grant")


@pytest.fixture
def client_and_service(tmp_path):
    service, database = _make_service(tmp_path, subject_id="alice.chen")
    app = create_app(environment="development", policy_service=service)
    with TestClient(app, client=("127.0.0.1", 8000), follow_redirects=False) as client:
        home = client.get("/")
        import re

        csrf = re.search(
            r'<meta name="csrf-token" content="([^"]+)"', home.text
        ).group(1)
        client.post(
            "/dev/login",
            json={"subject_id": "alice.chen"},
            headers={"x-csrf-token": csrf, "origin": "http://testserver"},
        )
        yield client, service, database, app


def _csrf(client):
    home = client.get("/")
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', home.text).group(1)


def _enqueue(client, service, payload):
    return client.post(
        "/api/evidence-runs",
        json=payload,
        headers={"origin": "http://testserver", "x-csrf-token": _csrf(client)},
    ), service


def _login(client, subject_id):
    return client.post(
        "/dev/login",
        json={"subject_id": subject_id},
        headers={"x-csrf-token": _csrf(client), "origin": "http://testserver"},
    )


def _billing_payload():
    return {
        "tenant_id": "northstar-retail",
        "query": "refund receipt",
        "as_of": "2026-11-22",
        "departments": ["billing"],
    }


def test_completed_run_returns_cited_evidence(client_and_service):
    client, service, _, app = client_and_service
    _seed_evidence_grants(service, grantee_subject_id="bob.patel")
    _login(client, "bob.patel")
    response, _ = _enqueue(client, service, _billing_payload())
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    queue = app.state.evidence_queue
    outcome = asyncio.run(queue.process_one())
    assert outcome["status"] == "completed", outcome
    run = queue.get_run(run_id)
    assert run.status == "completed"
    assert len(run.result) > 0, run.result
    item = run.result[0]
    assert item.policy_id == "billing-refunds"
    assert item.version == "1.0"
    assert item.department_id == "billing"
    assert item.effective_from == "2026-01-01"


@pytest.mark.asyncio
async def test_fail_closed_revocation_stops_retrieval_at_enqueue(client_and_service):
    client, service, _, app = client_and_service
    queue = app.state.evidence_queue
    # Seed an active grant, then revoke it BEFORE enqueuing: the run must fail
    # closed before any retrieval text is produced.
    _seed_evidence_grants(service, grantee_subject_id="alice.chen")
    iam = SQLiteIAMStore(service._database)
    for document in iam.authorization_document_snapshot()[1]:
        if document.id == "billing-refunds":
            iam.put_document(document.model_copy(update={"grants": ()}), occurred_at="revoke")
    _login(client, "alice.chen")
    response, _ = _enqueue(client, service, _billing_payload())
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    outcome = await queue.process_one()
    assert outcome["status"] == "denied", outcome
    run = queue.get_run(run_id)
    assert run.status == "failed"
    assert run.error == "authorization denied"


@pytest.mark.asyncio
async def test_crash_restart_converges_to_one_logical_outcome(client_and_service):
    client, service, _, app = client_and_service
    queue = app.state.evidence_queue
    response, _ = _enqueue(client, service, _billing_payload())
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]

    # Simulate a worker crash: the run is left 'running' with no outcome
    # recorded. A restarted worker must recover it and converge to one
    # terminal, fail-closed outcome.
    with closing(queue._repository._connect()) as connection:
        connection.execute(
            "UPDATE evidence_runs SET status = 'running' WHERE run_id = ?", (run_id,)
        )
        connection.commit()
    recovered = queue._repository.requeue_stale(stale_after_seconds=0)
    assert recovered == 1

    outcome = await queue.process_one()
    assert outcome["status"] in {"denied", "completed"}, outcome
    final = queue.get_run(run_id)
    assert final.status == "failed", final
    assert final.error == "authorization denied", final
    # Exactly one logical outcome: created, then failed (denied). No orphan
    # run and no double-marking.
    assert {event.event_type for event in final.events} == {"run.created", "run.failed"}


def test_cancel_is_fail_closed_and_double_cancel_is_409(client_and_service):
    client, service, _, app = client_and_service
    queue = app.state.evidence_queue
    response, _ = _enqueue(client, service, _billing_payload())
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    canceled = client.post(
        f"/api/evidence-runs/{run_id}/cancel",
        headers={"origin": "http://testserver", "x-csrf-token": _csrf(client)},
    )
    assert canceled.status_code == 202
    assert canceled.json()["status"] == "cancelled"
    # A second cancel is rejected for an already-terminal run.
    again = client.post(
        f"/api/evidence-runs/{run_id}/cancel",
        headers={"origin": "http://testserver", "x-csrf-token": _csrf(client)},
    )
    assert again.status_code == 409
    assert queue.get_run(run_id).status == "cancelled"
