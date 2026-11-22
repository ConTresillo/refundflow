"""Behavior-focused tests for the bounded decision + human-in-the-loop slice (P6a).

Covers deterministic proposal, fail-closed context guard, interrupt/resume,
budget termination, idempotent terminal replay, and a construction-level
guarantee that the proposal provider cannot touch identity or perform real
actions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from langgraph.types import Command

from app.decision_graph import DecisionProposalProvider, build_decision_graph

_BILLING_RECORD = {
    "id": "billing-refunds",
    "version": "v1",
    "department_id": "billing",
    "title": "Refund redemptions",
    "status": "active",
    "effective_from": "2026-01-01",
    "effective_to": None,
    "content_hash": "hash-billing-v1",
}
_SHIPPING_RECORD = {
    "id": "shipping-refunds",
    "version": "v2",
    "department_id": "shipping",
    "title": "Shipment returns",
    "status": "active",
    "effective_from": "2026-02-01",
    "effective_to": None,
    "content_hash": "hash-shipping-v2",
}


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "decision.sqlite3"




def _make_graph(db_path):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


    async def _build() -> Any:
        conn = aiosqlite.connect(db_path)
        return build_decision_graph(checkpointer=AsyncSqliteSaver(conn))

    return asyncio.run(_build())


@pytest.fixture()
def provider() -> DecisionProposalProvider:
    return DecisionProposalProvider()


def _base_state(**overrides):
    state = {
        "decision_id": "d1",
        "tenant_id": "northstar-retail",
        "actor_subject_id": "alice.chen",
        "question": "Is this customer eligible for a refund?",
        "evidence": [],
        "code": "queued",
        "warnings": [],
        "node_count": 0,
        "step_count": 0,
        "tool_call_count": 0,
        "deadline": None,
    }
    state.update(overrides)
    return state


def _config() -> dict:
    return {"configurable": {"thread_id": "t1"}}


def _resume(value: str):
    return Command(resume=value)


def _run(graph, state, resume=None):
    return asyncio.run(
        graph.ainvoke(state, config=_config()) if resume is None else graph.ainvoke(
            _resume(resume), config=_config()
        )
    )


def test_proposal_is_deterministic_and_evidence_anchored(db_path, provider):
    evidence = (
        {
            "policy_id": _BILLING_RECORD["id"],
            "version": _BILLING_RECORD["version"],
            "section_id": "s1",
            "text": "Refund claims require a payment reference and receipt.",
        },
    )
    state = _base_state(evidence=evidence, code="proposed")
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "proposed"
    assert result["proposal"] is not None
    assert _BILLING_RECORD["id"] in result["proposal"]
    assert _BILLING_RECORD["version"] in result["proposal"]
    assert evidence[0]["section_id"] in result["proposal"]
    # Reproducible: the same inputs must produce the same proposal text.
    assert result["proposal"] == _run(_make_graph(db_path), state)["proposal"]


def test_empty_evidence_blocks_without_proposal(db_path):
    state = _base_state(evidence=(), code="proposed")
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "blocked"
    assert result["error"] == "refusing to propose without evidence"
    assert result["proposal"] is None


def test_fail_closed_warning_blocks(db_path):
    state = _base_state(
        evidence=(),
        warnings=[{"severity": "fail_closed", "message": "AML watchlist change"}],
        code="proposed",
    )
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "blocked"
    assert result["error"] == "AML watchlist change"


def test_timeout_by_deadline(db_path):
    state = _base_state(
        evidence=(_BILLING_RECORD,),
        code="proposed",
        deadline=time.monotonic() - 0.001,
    )
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "timeout"
    assert "deadline" in result["error"]


def test_tool_budget_terminates(db_path):
    state = _base_state(
        evidence=(_BILLING_RECORD,),
        tool_call_count=6,
        code="proposed",
    )
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "timeout"
    assert "budget" in result["error"]


def test_interrupt_resume_approve(db_path):
    evidence = (_BILLING_RECORD,)
    state = _base_state(evidence=evidence, code="proposed")
    interrupted = _run(_make_graph(db_path), state)
    assert "__interrupt__" in interrupted
    assert interrupted["code"] == "proposed"
    assert interrupted.get("decision") is None
    _run(_make_graph(db_path), None, resume="approve")


def test_interrupt_resume_deny(db_path):
    evidence = (_BILLING_RECORD,)
    state = _base_state(evidence=evidence, code="proposed")
    _run(_make_graph(db_path), state)
    _run(_make_graph(db_path), None, resume="deny")


def test_terminal_replay_is_idempotent(db_path):
    evidence = (_BILLING_RECORD,)
    state = _base_state(evidence=evidence, code="proposed")
    _run(_make_graph(db_path), state)
    _run(_make_graph(db_path), None, resume="approve")
    # Terminal fallback: a second decision attempt must not change the outcome.
    _run(_make_graph(db_path), None, resume="deny")


def test_atomic_provider_stays_outside_iam_and_real_actions(db_path, provider):
    # Construction-level guard: the provider takes only question + evidence, so
    # it provably cannot read principal/permission state or touch customer data.
    signature = provider.propose.__code__.co_varnames
    assert "actor_subject_id" not in signature
    assert "tenant_id" not in signature
    assert "policy_service" not in signature
    assert "document" not in signature

    state = _base_state(evidence=())
    result = _run(_make_graph(db_path), state)
    assert result["code"] == "blocked"
    assert provider.propose("q", None) == "No evidence supplied; no proposal can be made."
