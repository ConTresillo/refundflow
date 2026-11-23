"""FastAPI routes for the bounded simulated decision workflow.

Exposes a single, reviewable user flow:
  POST   /api/decision-runs          -> 202 with run id (creates an interrupted run)
  GET    /api/decision-runs/{run_id} -> run status, evidence, proposal, events
  POST   /api/decision-runs/{run_id}/approve
                                  -> 200 with terminal decision (approve|deny)
  POST   /api/decision-runs/{run_id}/cancel -> 202 (fail-closed cancellation)
  GET    /api/decision-runs          -> tenant-scoped run cursor

Authorization is passed in through allowlisted callables so the route layer
never hard-codes a policy engine or identity provider. The approve command is
re-authorized at resume time: if the actor or the tenant membership changes, or
the approver no longer holds the approver role, the run fails closed and no
outcome is recorded.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from app.decision_graph import build_decision_graph
from app.decision_run import (
    _APPROVAL_DECISIONS,
    DecisionRun,
    DecisionRunRepository,
)
from app.dev_sessions import DevelopmentSession


class DecisionRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    actor_subject_id: str | None = Field(default=None, max_length=128)
    question: str = Field(min_length=1, max_length=300)
    evidence: list[dict[str, str]] = Field(min_length=1, max_length=50)
    as_of: str = Field(min_length=10, max_length=10)
    evidence_mode: str = Field(default="freshness_preferred", max_length=32)
    answer_cache: str = Field(default="off", max_length=16)
    idempotency_key: str | None = None
    command_id: str | None = None


class DecisionRunApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(min_length=1, max_length=16)
    approver_subject_id: str | None = Field(default=None, max_length=128)
    command_id: str | None = None


class DecisionCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str | None = None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _assert_interrupted(run: DecisionRun | dict[str, Any]) -> None:
    status = run.status if isinstance(run, DecisionRun) else run["status"]
    if status not in {"interrupted", "queued"}:
        raise HTTPException(status_code=409, detail="decision run is not awaiting approval")


def _validate_evidence(evidence: list[dict[str, str]]) -> None:
    for item in evidence:
        missing = {"policy_id", "version", "section_id"} - item.keys()
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"evidence item is missing policy fields: {sorted(missing)}",
            )


def register_decision_run_routes(
    app: FastAPI,
    repository: DecisionRunRepository,
    *,
    current_identity: Callable[[Request], Any],
    request_session: Callable[[Request], DevelopmentSession | None],
    require_same_origin: Callable[[Request], None],
    require_csrf: Callable[[DevelopmentSession | None, str | None], Any],
    authorize_run: Callable[[Request, Any, str], None] | None = None,
    authorize_approve: Callable[[Request, Any, DecisionRun], None] | None = None,
) -> None:
    """Attach the decision-run API to one development app instance."""

    def _default_authorize_run(request: Request, identity: Any, tenant_id: str) -> None:
        if identity.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="tenant mismatch")

    def _default_authorize_approve(
        request: Request, identity: Any, run: DecisionRun
    ) -> None:
        if identity.tenant_id != run.tenant_id:
            raise HTTPException(status_code=403, detail="approver is not in the run tenant")
        if identity.subject_id != run.actor_subject_id:
            raise HTTPException(status_code=403, detail="only the run owner may approve")

    def _authorize_run_tenant(request: Request, identity: Any, tenant_id: str) -> None:
        _default_authorize_run(request, identity, tenant_id)
        if authorize_run is not None:
            authorize_run(request, identity, tenant_id)

    def _authorize_owned_run(
        request: Request, identity: Any, tenant_id: str, actor_subject_id: str
    ) -> None:
        _authorize_run_tenant(request, identity, tenant_id)
        if identity.subject_id != actor_subject_id:
            raise HTTPException(status_code=403, detail="run is not owned by this actor")

    def _authorize_approval(request: Request, identity: Any, run: DecisionRun) -> None:
        _default_authorize_approve(request, identity, run)
        if authorize_approve is not None:
            authorize_approve(request, identity, run)

    default_authorize_run = _authorize_run_tenant
    default_authorize_approve = _authorize_approval

    def _require_interrupted(request: Request) -> None:
        run = repository.status(run_id=_run_id_from_request(request))
        if run is None:
            raise HTTPException(status_code=404, detail="decision run not found")
        if run["status"] not in {"interrupted", "queued"}:
            raise HTTPException(status_code=409, detail="decision run is not awaiting approval")

    def _run_id_from_request(request: Request) -> str:
        return request.path_params["run_id"]

    def _graph_config(run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": run_id}}

    # ------------------------------------------------------------ create
    async def _create_decision_run(
        request: Request,
        payload: DecisionRunPayload,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), x_csrf_token)

        default_authorize_run(request, identity, payload.tenant_id)

        if payload.actor_subject_id is None:
            actor_subject_id = identity.subject_id
        else:
            actor_subject_id = payload.actor_subject_id
        if payload.actor_subject_id is not None and payload.actor_subject_id != identity.subject_id:
            raise HTTPException(status_code=403, detail="actor mismatch")

        _validate_evidence(payload.evidence)

        try:
            from datetime import date

            date.fromisoformat(payload.as_of)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="as_of must be an ISO date") from error

        run_id = repository.create_run(
            tenant_id=payload.tenant_id,
            actor_subject_id=actor_subject_id,
            question=payload.question,
            evidence=payload.evidence,
            as_of=payload.as_of,
            evidence_mode=payload.evidence_mode,
            answer_cache=payload.answer_cache,
            idempotency_key=payload.idempotency_key,
            command_id=payload.command_id,
            now=_now_iso(),
        )

        try:
            from aiosqlite import connect as _connect
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await _connect(repository.database)
            checkpointer = AsyncSqliteSaver(conn)
            graph = build_decision_graph(checkpointer=checkpointer)

            state: dict[str, Any] = {
                "decision_id": run_id,
                "tenant_id": payload.tenant_id,
                "actor_subject_id": actor_subject_id,
                "question": payload.question,
                "evidence": tuple(payload.evidence),
                "code": "queued",
                "warnings": (),
                "node_count": 0,
                "step_count": 0,
                "tool_call_count": 0,
                "deadline": None,
            }

            result = await graph.ainvoke(
                state, config={"configurable": {"thread_id": run_id}}
            )
            await conn.close()

            repository.record_graph_output(
                run_id,
                proposal=result.get("proposal"),
                code=result.get("code"),
                error=result.get("error"),
                occurred_at=_now_iso(),
            )

            if result.get("__interrupt__"):
                return JSONResponse(
                    {
                        "run_id": run_id,
                        "status": "interrupted",
                        "message": "Decision proposal awaiting approval",
                        "event": "run.created",
                    },
                    status_code=202,
                    headers={"Cache-Control": "no-store"},
                )

            return JSONResponse(
                {
                    "run_id": run_id,
                    "status": result.get("code"),
                    "decision": result.get("decision"),
                    "proposal": result.get("proposal"),
                    "error": result.get("error"),
                    "event": "decision.recorded",
                },
                status_code=200,
                headers={"Cache-Control": "no-store"},
            )
        except Exception as error:  # fail closed on transient graph errors
            repository.append_event(
                run_id,
                "decision.failed",
                actor_subject_id=identity.subject_id,
                details={"run_id": run_id, "error": f"{type(error).__name__}: {error}"},
            )
            raise HTTPException(
                status_code=500,
                detail=f"decision run aborted: {type(error).__name__}: {error}",
            ) from error

    # --------------------------------------------------------------- get
    def _get_decision_run(request: Request) -> JSONResponse:
        identity = current_identity(request)
        run_id = _run_id_from_request(request)
        row = repository.status(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="decision run not found")
        _authorize_owned_run(
            request, identity, row["tenant_id"], row["actor_subject_id"]
        )
        return JSONResponse(
            {
                "run_id": row["run_id"],
                "tenant_id": row["tenant_id"],
                "actor_subject_id": row["actor_subject_id"],
                "question": row["question"],
                "evidence": json.loads(row["evidence_json"]),
                "as_of": row["as_of"],
                "evidence_mode": row["evidence_mode"],
                "answer_cache": row["answer_cache"],
                "status": row["status"],
                "code": row["code"],
                "proposal": row["proposal"],
                "decision": row["decision"],
                "error": row["error"],
                "events": repository.events(run_id),
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "cancelled_at": row["cancelled_at"],
            },
            headers={"Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------- audit
    def _get_decision_run_audit(request: Request) -> JSONResponse:
        run_id = _run_id_from_request(request)
        row = repository.status(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="decision run not found")
        identity = current_identity(request)
        _authorize_owned_run(
            request, identity, row["tenant_id"], row["actor_subject_id"]
        )
        return JSONResponse(
            {
                "run_id": row["run_id"],
                "events": [
                    {
                        "event_type": ev["event_type"],
                        "occurred_at": ev["occurred_at"],
                        "actor_subject_id": ev["actor_subject_id"],
                        "details": {
                            key: value
                            for key, value in json.loads(ev["details_json"]).items()
                            if key not in {"text", "question", "evidence", "query", "details"}
                        },
                    }
                    for ev in repository.events(run_id)
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    # -------------------------------------------------------------- approve
    async def _approve_decision_run(
        request: Request,
        payload: DecisionRunApproveRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), x_csrf_token)

        run_id = _run_id_from_request(request)
        row = repository.status(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="decision run not found")
        run = DecisionRun(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            actor_subject_id=row["actor_subject_id"],
            question=row["question"],
            evidence=json.loads(row["evidence_json"]),
            as_of=row["as_of"],
            evidence_mode=row["evidence_mode"],
            answer_cache=row["answer_cache"],
            status=row["status"],
            code=row["code"],
            proposal=row["proposal"],
            decision=row["decision"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
        )

        default_authorize_approve(request, identity, run)
        _assert_interrupted(run)

        if payload.decision not in _APPROVAL_DECISIONS:
            raise HTTPException(
                status_code=422, detail=f"decision must be approve or deny (got {payload.decision!r})"
            )

        # The request may repeat the authenticated actor ID for audit clarity,
        # but a client-supplied approver ID never grants access.
        if (
            payload.approver_subject_id is not None
            and payload.approver_subject_id != identity.subject_id
        ):
            raise HTTPException(status_code=403, detail="approver identity mismatch")

        try:
            from aiosqlite import connect as _connect
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await _connect(repository.database)
            checkpointer = AsyncSqliteSaver(conn)
            graph = build_decision_graph(checkpointer=checkpointer)

            if payload.decision == "approve":
                outcome = await graph.ainvoke(
                    Command(resume="approve"), config=_graph_config(run_id)
                )
            else:
                outcome = await graph.ainvoke(
                    Command(resume="deny"), config=_graph_config(run_id)
                )

            await conn.close()

            if outcome.get("code") in {"completed", "denied"}:
                repository.approve_run(
                    run_id=run_id,
                    decision=payload.decision,
                    actor_subject_id=identity.subject_id,
                    command_id=payload.command_id,
                    occurred_at=_now_iso(),
                )
                return JSONResponse(
                    {
                        "run_id": run_id,
                        "status": outcome.get("code"),
                        "decision": payload.decision,
                        "proposal": outcome.get("proposal"),
                        "error": outcome.get("error"),
                        "event": "decision.recorded",
                    },
                    status_code=200,
                    headers={"Cache-Control": "no-store"},
                )

            # Interrupted again: release the client to return to the approval UI.
            repository.append_event(
                run_id,
                "decision.interrupted",
                actor_subject_id=identity.subject_id,
                details={"run_id": run_id, "decision": payload.decision, "code": outcome.get("code")},
            )
            return JSONResponse(
                {
                    "run_id": run_id,
                    "status": outcome.get("code"),
                    "message": "Decision still awaiting approval",
                    "event": "decision.interrupted",
                },
                status_code=202,
                headers={"Cache-Control": "no-store"},
            )
        except Exception as error:  # fail closed on transient graph errors
            repository.append_event(
                run_id,
                "decision.failed",
                actor_subject_id=identity.subject_id,
                details={"run_id": run_id, "error": f"{type(error).__name__}: {error}"},
            )
            raise HTTPException(
                status_code=500, detail=f"decision run aborted: {type(error).__name__}: {error}"
            ) from error

    # -------------------------------------------------------------- cancel
    def _cancel_decision_run(request: Request, payload: DecisionCancelRequest) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), request.headers.get("x-csrf-token"))
        run_id = _run_id_from_request(request)
        row = repository.status(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="decision run not found")
        _authorize_owned_run(
            request, identity, row["tenant_id"], row["actor_subject_id"]
        )
        _assert_interrupted(row)
        repository.cancel_run(
            run_id=run_id,
            actor_subject_id=identity.subject_id,
            command_id=payload.command_id,
            occurred_at=_now_iso(),
        )
        return JSONResponse(
            {"run_id": run_id, "status": "cancelled", "event": "run.cancelled"},
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    # -------------------------------------------------------------- list
    def _list_decision_runs(request: Request) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), request.headers.get("x-csrf-token"))
        default_authorize_run(request, identity, identity.tenant_id)
        rows = repository.list_runs(
            identity.tenant_id, actor_subject_id=identity.subject_id, limit=50
        )
        return JSONResponse(
            [DecisionRun.model_validate(row).model_dump() for row in rows],
            headers={"Cache-Control": "no-store"},
        )

    # --------------------------------------------------------------- routes
    @app.get("/api/decision-runs/{run_id}", response_class=JSONResponse)
    def decision_run_get(request: Request) -> JSONResponse:
        return _get_decision_run(request)

    @app.get("/api/decision-runs/{run_id}/audit", response_class=JSONResponse)
    def decision_run_audit(request: Request) -> JSONResponse:
        return _get_decision_run_audit(request)

    @app.post("/api/decision-runs/{run_id}/approve", response_class=JSONResponse)
    async def decision_run_approve(
        request: Request,
        payload: DecisionRunApproveRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return await _approve_decision_run(request, payload, x_csrf_token)

    @app.post("/api/decision-runs/{run_id}/cancel", response_class=JSONResponse)
    async def decision_run_cancel(
        request: Request,
        payload: DecisionCancelRequest,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return _cancel_decision_run(request, payload)

    @app.get("/api/decision-runs", response_class=JSONResponse)
    def decision_run_list(request: Request) -> JSONResponse:
        return _list_decision_runs(request)

    @app.post("/api/decision-runs", response_class=JSONResponse)
    async def decision_run_create(
        request: Request,
        payload: DecisionRunPayload,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return await _create_decision_run(request, payload, x_csrf_token)
