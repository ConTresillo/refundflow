"""Behavior-focused tests for the bounded simulated decision workflow (P6a/P6b).

Covers:
  - the deterministic proposal, fail-closed context guard, interrupt/resume
  - bounded budgets and terminal replay (graph-level, from tests/test_decision_graph.py)
  - the durable decision-run repository: idempotent create, cancel, single
    approval record, terminal outcomes
  - the HTTP surface: create -> 202 interrupted, approve -> terminal,
    wrong-tenant/wrong-role approver denial, cancel, fail-closed ACL change on resume
"""

from __future__ import annotations

import types
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.decision_run import DecisionRun, DecisionRunRepository
from app.decision_run_routes import register_decision_run_routes


def _make_app(
    repository: DecisionRunRepository,
    current_identity: Callable[[Request], Any] | None = None,
    authorize_run: Callable[[Request, Any, str], None] | None = None,
    authorize_approve: Callable[[Request, Any, DecisionRun], None] | None = None,
) -> FastAPI:
    app = FastAPI()

    if current_identity is None:
        def _identity_fn(request: Any) -> Any:
            return _identity("alice.chen", "northstar-retail")
    else:
        _identity_fn = current_identity

    request_session = lambda request: None
    require_same_origin = lambda request: None
    require_csrf = lambda session, token: None

    register_decision_run_routes(
        app,
        repository,
        current_identity=_identity_fn,
        request_session=request_session,
        require_same_origin=require_same_origin,
        require_csrf=require_csrf,
        authorize_run=authorize_run,
        authorize_approve=authorize_approve,
    )
    return app


def _identity(subject_id: str, tenant_id: str) -> Any:
    return types.SimpleNamespace(subject_id=subject_id, tenant_id=tenant_id, roles=[])


def _sample_evidence() -> list[dict[str, str]]:
    return [
        {
            "policy_id": "billing-refunds",
            "version": "v1",
            "section_id": "s1",
            "text": "Refund claims require a payment reference and receipt.",
        }
    ]


@pytest.fixture()
def repository(tmp_path: Path) -> DecisionRunRepository:
    return DecisionRunRepository(tmp_path / "decision.sqlite3")


# --------------------------------------------------------------------------
# Repository-level behavior
# --------------------------------------------------------------------------
def test_repository_create_is_idempotent(repository: DecisionRunRepository) -> None:
    evidence = _sample_evidence()
    first = repository.create_run(
        tenant_id="northstar-retail",
        actor_subject_id="alice.chen",
        question="What is the refund policy?",
        evidence=evidence,
        as_of="2026-11-22",
        evidence_mode="freshness_preferred",
        answer_cache="off",
    )
    second = repository.create_run(
        tenant_id="northstar-retail",
        actor_subject_id="alice.chen",
        question="What is the refund policy?",
        evidence=evidence,
        as_of="2026-11-22",
        evidence_mode="freshness_preferred",
        answer_cache="off",
    )
    assert first == second
    assert repository.status(first)["status"] == "interrupted"


def test_repository_cancel_and_terminal_outcomes(repository: DecisionRunRepository) -> None:
    # A cancellable (interrupted) run.
    run = repository.create_run(
        tenant_id="northstar-retail",
        actor_subject_id="alice.chen",
        question="q",
        evidence=_sample_evidence(),
        as_of="2026-11-22",
        evidence_mode="freshness_preferred",
        answer_cache="off",
    )
    assert repository.cancel_run(run, "alice.chen", "2026-11-22T00:00:00+00:00") is True
    assert repository.status(run)["status"] == "cancelled"
    # Terminal runs reject a second cancellation request.
    assert repository.cancel_run(run, "alice.chen", "2026-11-22T00:00:00+00:00") is False

    # An approve run records its terminal outcome once.
    run2 = repository.create_run(
        tenant_id="northstar-retail",
        actor_subject_id="alice.chen",
        question="Should this item be replaced or refunded?",
        evidence=_sample_evidence(),
        as_of="2026-11-22",
        evidence_mode="freshness_preferred",
        answer_cache="off",
    )
    repository.approve_run(run2, "approve", "erin.wu", "2026-11-22T00:00:00+00:00")
    assert repository.status(run2)["code"] == "completed"
    # A second approval is rejected (fail-closed) and must not overwrite the outcome.
    with pytest.raises(LookupError):
        repository.approve_run(run2, "deny", "erin.wu", "2026-11-22T00:00:00+00:00")
    assert repository.status(run2)["decision"] == "approve"
    assert repository.status(run2)["code"] == "completed"


# --------------------------------------------------------------------------
# Route-level behavior
# --------------------------------------------------------------------------
def _graph_result(interrupted: bool, code: str = "completed", proposal: str = "proposal", decision: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "proposal": proposal, "error": None}
    if interrupted:
        result["__interrupt__"] = True
        result["decision"] = None
    if decision is not None:
        result["decision"] = decision
    return result


@pytest.fixture()
def decision_app(repository: DecisionRunRepository, tmp_path: Path) -> FastAPI:
    return _make_app(repository)


class _FakeGraph:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self._result = result or {}
        self.ainvoke = AsyncMock(return_value=self._result)

    async def ainvoke(self, state: Any = None, config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        return self.ainvoke.return_value


def _register_graph_result(app: Any, result: dict[str, Any]) -> None:
    app.test_client().app.state._decision_result = result


def test_route_create_interrupts_then_approves(decision_app: Any, repository: DecisionRunRepository) -> None:
    result = {"code": "proposed", "proposal": "approve the claim", "__interrupt__": True}
    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph(result=result)):
        client = TestClient(decision_app)
        response = client.post(
            "/api/decision-runs",
            json={
                "tenant_id": "northstar-retail",
                "actor_subject_id": "alice.chen",
                "question": "Is this customer eligible for a refund?",
                "evidence": _sample_evidence(),
                "as_of": "2026-11-22",
            },
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "interrupted"
    assert body["run_id"]

    run_id = body["run_id"]
    assert repository.status(run_id)["status"] == "interrupted"
    assert repository.status(run_id)["proposal"] == "approve the claim"

    # Resume with an approval command -> terminal completed.
    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph(result={"code": "completed", "decision": "approve"})):
        response = client.post(f"/api/decision-runs/{run_id}/approve", json={"decision": "approve"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "approve"
    assert body["status"] == "completed"


def test_route_wrong_tenant_approver_denied(repository: DecisionRunRepository) -> None:
    # P6a: a wrong-tenant actor cannot be the approver. The creating identity is
    # alice.chen (northstar-retail); the resume identity dana.ross belongs to a
    # different tenant and must be refused at the fail-closed gate.
    run = repository.create_run(
        tenant_id="northstar-retail",
        actor_subject_id="alice.chen",
        question="q",
        evidence=_sample_evidence(),
        as_of="2026-11-22",
        evidence_mode="freshness_preferred",
        answer_cache="off",
    )
    def authorize_approve_out_of_tenant(request, identity, run):
        # Designated approver must reside in the run tenant.
        if identity.subject_id == "dana.ross" and identity.tenant_id != run.tenant_id:
            raise HTTPException(status_code=403, detail="tenant mismatch")
    app = FastAPI()
    register_decision_run_routes(
        app,
        repository,
        current_identity=lambda r: _identity("dana.ross", "contoso-license"),
        request_session=lambda r: None,
        require_same_origin=lambda r: None,
        require_csrf=lambda s, t: None,
        authorize_approve=authorize_approve_out_of_tenant,
    )
    client = TestClient(app)
    response = client.post(
        f"/api/decision-runs/{run}/approve",
        json={"decision": "approve", "approver_subject_id": "dana.ross"},
    )
    assert response.status_code == 403


def test_route_wrong_role_approver_denied(repository: DecisionRunRepository) -> None:
    # P6b: a right-tenant, wrong-role approver is refused at the fail-closed
    # resume gate. The run is owned by alice.chen; the request identity is
    # carol.wu (same tenant, no approver role) and must be denied.
    def current_identity(request: Any) -> Any:
        # The create request carries the owner; the resume request uses a
        # non-owner identity so the run-level deny check fires.
        if "/approve" in request.url.path:
            return _identity("carol.wu", "northstar-retail")
        return _identity("alice.chen", "northstar-retail")

    app = _make_app(repository, current_identity=current_identity)
    client = TestClient(app)

    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph({"code": "proposed", "__interrupt__": True})):
        created = client.post(
            "/api/decision-runs",
            json={
                "tenant_id": "northstar-retail",
                "actor_subject_id": "alice.chen",
                "question": "q",
                "evidence": _sample_evidence(),
                "as_of": "2026-11-22",
            },
        )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph({"code": "completed", "decision": "approve"})):
        response = client.post(
            f"/api/decision-runs/{run_id}/approve",
            json={"decision": "approve", "approver_subject_id": "bob.patel"},
        )
    assert response.status_code == 403


def test_route_fail_closed_on_acl_change_at_resume(repository: DecisionRunRepository) -> None:
    """An ACL change between interrupt and resume fails the approval closed."""
    call_count = {"n": 0}

    def flaky_authorize(request: Any, identity: Any, run: Any) -> None:
        call_count["n"] += 1
        # Membership/authorization revoked between interrupt and resume.
        if call_count["n"] == 1:
            raise HTTPException(status_code=403, detail="membership revoked")

    app = _make_app(repository, authorize_approve=flaky_authorize)
    client = TestClient(app)

    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph({"code": "proposed", "__interrupt__": True})):
        created = client.post(
            "/api/decision-runs",
            json={
                "tenant_id": "northstar-retail",
                "actor_subject_id": "alice.chen",
                "question": "q",
                "evidence": _sample_evidence(),
                "as_of": "2026-11-22",
            },
        )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    with patch("app.decision_run_routes.build_decision_graph", return_value=_FakeGraph({"code": "completed", "decision": "approve"})):
        response = client.post(
            f"/api/decision-runs/{run_id}/approve",
            json={"decision": "approve", "approver_subject_id": "alice.chen"},
        )
    assert response.status_code == 403
    assert call_count["n"] == 1
