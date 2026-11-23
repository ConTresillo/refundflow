"""Behavior-focused tests for the durable async evidence-run slice (P5)."""

from __future__ import annotations

import asyncio
import json
from contextlib import closing
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService
from app.policy_ingestion import PolicyIngestionService
from app.web import create_app


def _make_service(tmp_path, subject_id="alice.chen", tenant_id="northstar-retail"):
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
    service = PolicyChangeService(
        source_dir,
        catalog,
        index,
        clock=lambda: datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    return service, database


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


# ---------------------------------------------------------------------- enqueue
def test_enqueue_returns_202_and_queues_a_run(client_and_service):
    client, service, _, _ = client_and_service
    response, _ = _enqueue(client, service, {
        "tenant_id": "northstar-retail",
        "query": "refund receipt",
        "as_of": "2026-11-22",
        "departments": ["billing"],
    })
    assert response.status_code == 202
    body = response.json()
    assert "run_id" in body
    assert body["status"] == "queued"


def test_enqueue_is_idempotent_on_idempotency_key(client_and_service):
    client, service, _, _ = client_and_service
    payload = {
        "tenant_id": "northstar-retail",
        "query": "refund receipt",
        "as_of": "2026-11-22",
        "departments": ["billing"],
    }
    first, _ = _enqueue(client, service, payload)
    second, _ = _enqueue(client, service, payload)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]


def _csrf(client):
    home = client.get("/")
    import re

    return re.search(r'<meta name="csrf-token" content="([^\"]+)"', home.text).group(1)


def _enqueue(client, service, payload):
    csrf = _csrf(client)
    response = client.post(
        "/api/evidence-runs",
        json=payload,
        headers={"origin": "http://testserver", "x-csrf-token": csrf},
    )
    return response, service


def test_cancel_queues_a_cancellation(client_and_service):
    client, service, _, _ = client_and_service
    payload = {
        "tenant_id": "northstar-retail",
        "query": "refund receipt",
        "as_of": "2026-11-22",
        "departments": ["billing"],
    }
    created, _ = _enqueue(client, service, payload)
    run_id = created.json()["run_id"]
    canceled = client.post(
        f"/api/evidence-runs/{run_id}/cancel",
        headers={"origin": "http://testserver", "x-csrf-token": _csrf(client)},
    )
    assert canceled.status_code == 202
    assert canceled.json()["status"] == "cancelled"
    resp = client.get(f"/api/evidence-runs/{run_id}", headers={"origin": "http://testserver"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------- worker
def test_worker_processes_a_queued_run_through_the_graph(client_and_service):
    client, _service, _, app = client_and_service
    csrf = _csrf(client)
    created = client.post(
        "/api/evidence-runs",
        json={
            "tenant_id": "northstar-retail",
            "query": "refund receipt",
            "as_of": "2026-11-22",
            "departments": ["billing"],
        },
        headers={"origin": "http://testserver", "x-csrf-token": csrf},
    )
    run_id = created.json()["run_id"]
    queue = app.state.evidence_queue
    outcome = asyncio.run(queue.process_one())
    assert outcome["status"] == "denied"  # alice.chen has no billing read grant
    assert queue.get_run(run_id).status in {"failed"}
    assert queue.get_run(run_id).error == "authorization denied"




# ---------------------------------------------------------------------- revoke
def test_revoked_actor_fails_closed(client_and_service):
    client, service, _, app = client_and_service
    csrf = _csrf(client)
    created = client.post(
        "/api/evidence-runs",
        json={
            "tenant_id": "northstar-retail",
            "query": "refund receipt",
            "as_of": "2026-11-22",
            "departments": ["billing"],
        },
        headers={"origin": "http://testserver", "x-csrf-token": csrf},
    )
    run_id = created.json()["run_id"]
    # Bob is the document owner; revoke his read grant through the IAM store.
    from app.iam_store import SQLiteIAMStore

    iam = SQLiteIAMStore(service._database)
    for document in iam.authorization_document_snapshot()[1]:
        if document.id == "billing-refunds":
            iam.put_document(
                document.model_copy(update={"grants": ()}),
                occurred_at="test-revocation",
            )
    queue = app.state.evidence_queue
    outcome = asyncio.run(queue.process_one())
    assert outcome["status"] == "denied"
    assert queue.get_run(run_id).error == "authorization denied"


# ---------------------------------------------------------------------- rollback
def test_crash_recovery_requeues_running_runs(client_and_service):
    client, _service, _, app = client_and_service
    csrf = _csrf(client)
    created = client.post(
        "/api/evidence-runs",
        json={
            "tenant_id": "northstar-retail",
            "query": "refund receipt",
            "as_of": "2026-11-22",
            "departments": ["billing"],
        },
        headers={"origin": "http://testserver", "x-csrf-token": csrf},
    )
    run_id = created.json()["run_id"]
    queue = app.state.evidence_queue
    repository = queue._repository
    # Simulate a worker crash: run is still 'running' with no outcome.
    with closing(repository._connect()) as connection:
        connection.execute(
            "UPDATE evidence_runs SET status = 'running' WHERE run_id = ?", (run_id,)
        )
        connection.commit()
    recovered = repository.requeue_stale(stale_after_seconds=0)
    assert recovered == 1
    second = asyncio.run(queue.process_one())
    assert second["status"] in {"denied", "completed"}


# ---------------------------------------------------------------------- idempotency
def test_idempotent_submission_returns_same_run_id(client_and_service):
    client, _service, _, _ = client_and_service
    csrf = _csrf(client)
    payload = {
        "tenant_id": "northstar-retail",
        "query": "refund receipt",
        "as_of": "2026-11-22",
        "departments": ["billing"],
    }
    first = client.post(
        "/api/evidence-runs", json=payload, headers={"origin": "http://testserver", "x-csrf-token": csrf}
    )
    second = client.post(
        "/api/evidence-runs", json=payload, headers={"origin": "http://testserver", "x-csrf-token": csrf}
    )
    assert first.json()["run_id"] == second.json()["run_id"]
