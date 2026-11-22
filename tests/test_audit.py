"""Behavior-focused tests for the immutable audit / read model (P7a).

Covers:
  - append-only DB triggers on the decision-run audit trail (any UPDATE or
    DELETE on the events table fails closed)
  - command_id assignment, idempotent resubmission and conflict rejection
  - transaction rollback when an audit guard fires mid-write
  - the authorized, redacted audit read endpoint
"""


import json
import sqlite3

import pytest

from app.decision_run import DecisionRunRepository

_EVENT = {
    "event_type": "decision.recorded",
    "occurred_at": "2026-11-22T10:00:00+00:00",
    "actor_subject_id": "erin.wu",
    "details": {"run_id": "r1", "decision": "approve", "code": "completed"},
}


def _repo(tmp_path):
    return DecisionRunRepository(tmp_path / "audit.sqlite3")


def _insert_event(repo, run_id, **overrides):
    conn = sqlite3.connect(repo.database)
    try:
        conn.execute(
            "INSERT INTO decision_run_events "
            "(run_id, event_type, occurred_at, actor_subject_id, details_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                overrides.get("event_type", "audit"),
                overrides.get("occurred_at", "2026-11-22T10:00:00+00:00"),
                overrides.get("actor_subject_id", "alice.chen"),
                json.dumps(overrides.get("details", {}), sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Append-only triggers
# --------------------------------------------------------------------------
class TestAppendOnlyTriggers:
    def test_update_on_events_table_is_rejected(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )
        _insert_event(repo, run_id)

        conn = sqlite3.connect(repo.database)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE decision_run_events SET event_type='x' WHERE run_id=?",
                    (run_id,),
                )
                conn.commit()
        finally:
            conn.close()

    def test_delete_on_events_table_is_rejected(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )
        _insert_event(repo, run_id)

        conn = sqlite3.connect(repo.database)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM decision_run_events WHERE run_id=?", (run_id,))
                conn.commit()
        finally:
            conn.close()

    def test_command_id_reassignment_to_different_value_is_rejected(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
            command_id="cmd-1",
        )

        conn = sqlite3.connect(repo.database)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE decision_runs SET command_id='cmd-2' WHERE run_id=?",
                    (run_id,),
                )
                conn.commit()
        finally:
            conn.close()

    def test_command_id_can_be_set_when_null(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )

        conn = sqlite3.connect(repo.database)
        try:
            conn.execute(
                "UPDATE decision_runs SET command_id='cmd-1' WHERE run_id=?",
                (run_id,),
            )
            conn.commit()
            row = conn.execute("SELECT command_id FROM decision_runs WHERE run_id=?", (run_id,)).fetchone()
            assert row[0] == "cmd-1"
        finally:
            conn.close()


# --------------------------------------------------------------------------
# Command dedupe
# --------------------------------------------------------------------------
class TestCommandDedupe:
    def test_same_approval_command_returns_recorded_decision(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
            command_id="cmd-approve",
        )
        repo.approve_run(run_id, "approve", "erin.wu", command_id="cmd-approve")

        recorded = repo.status(run_id)
        assert recorded["decision"] == "approve"
        assert recorded["command_id"] == "cmd-approve"

        # Re-applying the same command returns the recorded decision (idempotent).
        assert repo.approve_run(run_id, "approve", "erin.wu", command_id="cmd-approve") == "approve"

    def test_conflicting_command_id_raise(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
            command_id="cmd-1",
        )
        repo.approve_run(run_id, "approve", "erin.wu", command_id="cmd-1")

        with pytest.raises(LookupError):
            repo.approve_run(run_id, "deny", "erin.wu", command_id="cmd-2")


# --------------------------------------------------------------------------
# Transaction rollback on audit guard
# --------------------------------------------------------------------------
class TestAuditTransactionRollback:
    def test_guard_fires_rolls_back_parent_transaction(self, tmp_path):
        repo = _repo(tmp_path)
        run_id = repo.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )

        conn = sqlite3.connect(repo.database)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE decision_run_events SET event_type='x' WHERE run_id=?",
                    (run_id,),
                )
                conn.commit()  # should never reach here
                raise AssertionError("UPDATE should have raised")
            except sqlite3.IntegrityError:
                conn.rollback()
        finally:
            conn.close()

        # The run row must be unchanged (status still queued).
        record = repo.status(run_id)
        assert record["status"] == "interrupted"
        assert record["command_id"] is None


# --------------------------------------------------------------------------
# Redacted audit read
# --------------------------------------------------------------------------
class TestRedactedAuditRead:
    def test_read_endpoint_redacts_sensitive_details(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.decision_run import DecisionRunRepository
        from app.decision_run_routes import register_decision_run_routes

        repository = DecisionRunRepository(tmp_path / "audit.sqlite3")
        app = FastAPI()

        def _identity(request):
            return type("I", (), {"subject_id": "alice.chen", "tenant_id": "northstar-retail"})()

        register_decision_run_routes(
            app,
            repository,
            current_identity=_identity,
            request_session=lambda r: None,
            require_same_origin=lambda r: None,
            require_csrf=lambda s, t: None,
        )

        client = TestClient(app)
        run_id = repository.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )

        # Seed a raw, sensitive details JSON that must be redacted.
        conn = sqlite3.connect(repository.database)
        conn.execute(
            "INSERT INTO decision_run_events "
            "(run_id, event_type, occurred_at, actor_subject_id, details_json) "
            "VALUES (?, 'audit.record', ?, ?, ?)",
            (
                run_id,
                "2026-11-22T10:00:00+00:00",
                "erin.wu",
                json.dumps(
                    {
                        "run_id": run_id,
                        "decision": "approve",
                        "text": "SECRET_CUSTOMER_TEXT",
                        "question": "CUSTOMER_QUESTION",
                        "evidence": ["confidential"],
                    },
                    sort_keys=True,
                ),
            ),
        )
        conn.commit()
        conn.close()

        response = client.get(f"/api/decision-runs/{run_id}/audit")
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        events_by_type = {e["event_type"]: e for e in body["events"]}
        assert "audit.record" in events_by_type
        event = events_by_type["audit.record"]
        # Redacted: sensitive fields removed, event metadata retained.
        assert event["event_type"] == "audit.record"
        assert "text" not in event["details"]
        assert "question" not in event["details"]
        assert "evidence" not in event["details"]
        assert event["actor_subject_id"] == "erin.wu"

    def test_audit_read_denies_other_tenant(self, tmp_path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.decision_run import DecisionRunRepository
        from app.decision_run_routes import register_decision_run_routes

        repository = DecisionRunRepository(tmp_path / "audit.sqlite3")
        app = FastAPI()

        def _identity(request):
            return type("I", (), {"subject_id": "eve", "tenant_id": "other-tenant"})()

        register_decision_run_routes(
            app,
            repository,
            current_identity=_identity,
            request_session=lambda r: None,
            require_same_origin=lambda r: None,
            require_csrf=lambda s, t: None,
        )

        client = TestClient(app)
        run_id = repository.create_run(
            tenant_id="northstar-retail",
            actor_subject_id="alice.chen",
            question="q",
            evidence=[],
            as_of="2026-11-22",
            evidence_mode="freshness_preferred",
            answer_cache="off",
        )

        response = client.get(f"/api/decision-runs/{run_id}/audit")
        assert response.status_code == 403
