"""FastAPI routes for durable async evidence runs.

Exposes:
  POST   /api/evidence-runs          -> 202 with run id (idempotent)
  GET    /api/evidence-runs/{run_id} -> run status, events, result
  GET    /api/evidence-runs          -> cursor-paginated run list
  POST   /api/evidence-runs/{run_id}/cancel -> 202 (fail-closed cancellation)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.dev_sessions import DevelopmentSession
from app.evidence_queue import EvidenceQueue
from app.retrieval import SQLiteEvidenceRetriever


class EvidenceRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    actor_subject_id: str | None = Field(default=None, max_length=128)
    query: str = Field(min_length=1, max_length=300)
    departments: list[str] = Field(min_length=1, max_length=20)
    as_of: str = Field(min_length=10, max_length=10)
    evidence_mode: str = Field(default="freshness_preferred", max_length=32)
    answer_cache: str = Field(default="off", max_length=16)
    max_retries: int = Field(default=10, ge=0, le=100)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _validate_payload(payload: EvidenceRunPayload) -> None:
    if payload.evidence_mode not in SQLiteEvidenceRetriever.FRESHNESS_MODES:
        raise HTTPException(status_code=422, detail="evidence_mode must be freshness_preferred, ttl_snapshot or critical")
    if payload.answer_cache not in {"off", "exact"}:
        raise HTTPException(status_code=422, detail="answer_cache must be off or exact")
    try:
        datetime.fromisoformat(payload.as_of)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="as_of must be an ISO date") from error


def register_evidence_run_routes(
    app: FastAPI,
    policy_service: Any,
    queue: EvidenceQueue,
    *,
    current_identity: Callable[[Request], Any],
    request_session: Callable[[Request], DevelopmentSession | None],
    require_same_origin: Callable[[Request], None],
    require_csrf: Callable[[DevelopmentSession | None, str | None], Any],
) -> None:
    """Attach the async evidence run API to one development app instance."""

    def _create_evidence_run(
        request: Request,
        payload: EvidenceRunPayload,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), x_csrf_token)
        if payload.tenant_id != identity.tenant_id:
            raise HTTPException(status_code=403, detail="tenant mismatch")
        actor_subject_id = payload.actor_subject_id or identity.subject_id
        if payload.actor_subject_id is not None and payload.actor_subject_id != identity.subject_id:
            raise HTTPException(status_code=403, detail="actor mismatch")
        if not payload.departments:
            raise HTTPException(status_code=422, detail="departments required")
        _validate_payload(payload)
        run_id = queue.enqueue(
            tenant_id=payload.tenant_id,
            actor_subject_id=actor_subject_id,
            query=payload.query,
            departments=payload.departments,
            as_of=payload.as_of,
            evidence_mode=payload.evidence_mode,
            answer_cache=payload.answer_cache,
            idempotency_key=None,
            max_retries=payload.max_retries,
        )
        return JSONResponse(
            {
                "run_id": run_id,
                "status": "queued",
                "enqueued_at": _now_iso(),
            },
            status_code=202,
        )

    @app.post("/api/evidence-runs", status_code=202)
    async def evidence_run_create(
        request: Request,
        payload: EvidenceRunPayload,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        return _create_evidence_run(request, payload, x_csrf_token)

    @app.get("/api/evidence-runs/{run_id}")
    def evidence_run_get(request: Request, run_id: str) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        run = queue.get_run(run_id)
        if run.tenant_id != identity.tenant_id:
            raise HTTPException(status_code=403, detail="run not in your tenant")
        return JSONResponse(
            {
                "run_id": run.run_id,
                "status": run.status,
                "error": run.error,
                "retry_count": run.retry_count,
                "result": [item.model_dump(mode="json") for item in run.result],
                "events": [item.model_dump(mode="json") for item in run.events],
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "cancelled_at": run.cancelled_at,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/evidence-runs")
    def evidence_run_list(
        request: Request,
        cursor: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
    ) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        runs, next_cursor = queue._repository.list_runs(identity.tenant_id, limit=limit, cursor=cursor)
        return JSONResponse(
            {"runs": runs, "next_cursor": next_cursor},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/evidence-runs/{run_id}/cancel", status_code=202)
    async def evidence_run_cancel(
        request: Request,
        run_id: str,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        identity = current_identity(request)
        require_same_origin(request)
        require_csrf(request_session(request), x_csrf_token)
        run = queue.get_run(run_id)
        if run.tenant_id != identity.tenant_id:
            raise HTTPException(status_code=403, detail="run not in your tenant")
        if queue._repository.is_terminal(run_id):
            raise HTTPException(status_code=409, detail=f"run is already {run.status}")
        queue.cancel(run_id)
        return JSONResponse(
            {"run_id": run_id, "status": "cancelled"},
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )
