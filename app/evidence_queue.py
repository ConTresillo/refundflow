"""Durable evidence run queue and worker.

The queue is a thin durable work queue over the shared SQLite outbox in
``app/evidence_run.py``: enqueue is an outbox insert, claim is a bounded
single-row update, and processing is the async LangGraph evidence pipeline
(build via :mod:`app.evidence_runner`). It provides bounded retries,
cancellation, dead-lettering and crash recovery (requeue of orphaned runs).
The event loop is never blocked by synchronous SQLite: the runner offloads the
lexical retrier with ``asyncio.to_thread`` and the graph itself is async.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.evidence_run import (
    EvidenceRun,
    EvidenceRunEvent,
    EvidenceRunRepository,
    _now_iso,
)
from app.evidence_runner import EvidenceRunner


def _event(
    event_type: str,
    *,
    actor_subject_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> EvidenceRunEvent:
    return EvidenceRunEvent(
        event_type=event_type,
        occurred_at=_now_iso(),
        actor_subject_id=actor_subject_id,
        details=details or {},
    )


class EvidenceQueue:
    """Durable, outbox-backed queue for async evidence runs."""

    def __init__(
        self,
        repository: EvidenceRunRepository,
        runner: EvidenceRunner,
        *,
        max_retries: int = 10,
        retry_backoff_seconds: float = 0.0,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    # ------------------------------------------------------------- enqueue
    def enqueue(
        self,
        *,
        tenant_id: str,
        actor_subject_id: str | None,
        query: str,
        departments: list[str],
        as_of: str,
        evidence_mode: str = "freshness_preferred",
        answer_cache: str = "off",
        idempotency_key: str | None = None,
        max_retries: int | None = None,
    ) -> str:
        """Insert one outbox row and return its run id."""
        return self._repository.create_run(
            tenant_id=tenant_id,
            actor_subject_id=actor_subject_id,
            query=query,
            departments=departments,
            as_of=as_of,
            evidence_mode=evidence_mode,
            answer_cache=answer_cache,
            idempotency_key=idempotency_key,
            max_retries=max_retries or self._max_retries,
        )

    # -------------------------------------------------------------- guard
    def cancel(self, run_id: str) -> bool:
        """Mark a queued or running run cancelled (fail-closed)."""
        return self._repository.mark_cancelled(
            run_id,
            events=[_event("run.cancelled", details={"run_id": run_id})],
        )

    def get_run(self, run_id: str) -> EvidenceRun:
        return self._repository.status(run_id)

    # ------------------------------------------------------------ process
    async def process_one(self) -> dict[str, Any] | None:
        """Claim one queued run and run the evidence pipeline for it."""
        row = self._repository.claim()
        if row is None:
            return None
        return await self._process_run(row)

    async def drain(self, max_runs: int = 100) -> list[dict[str, Any]]:
        """Process up to ``max_runs`` queued runs."""
        outcomes: list[dict[str, Any]] = []
        for _ in range(max_runs):
            outcome = await self.process_one()
            if outcome is None:
                break
            outcomes.append(outcome)
        return outcomes

    async def _process_run(self, row: Any) -> dict[str, Any]:
        run_id = row["run_id"]
        if self._repository.is_cancelled(run_id):
            return {"run_id": run_id, "status": "cancelled", "reason": "rejected before processing"}

        try:
            result = await self._runner.run(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - persist/retry all unexpected worker failures
            retry_count = int(row["retry_count"]) + 1
            if retry_count > self._max_retries:
                self._repository.mark_failed(
                    run_id,
                    error_json={
                        "error": type(error).__name__,
                        "message": str(error),
                        "retry_count": retry_count,
                    },
                    retry_count=retry_count,
                    max_retries=self._max_retries,
                    events=[_event("run.failed", details={"error": type(error).__name__})],
                )
                return {"run_id": run_id, "status": "dead_letter", "error": type(error).__name__}
            self._repository.requeue_retry(run_id, retry_count)
            return {"run_id": run_id, "status": "retry", "retry_count": retry_count}

        if result.status == "completed":
            self._repository.mark_completed(
                run_id,
                result.model_dump_json(),
                events=[
                    _event(
                        "run.completed",
                        actor_subject_id=result.run_id,
                        details={
                            "status": "completed",
                            "evidence_count": len(result.evidence),
                        },
                    )
                ],
            )
            return {"run_id": run_id, "status": "completed", "evidence_count": len(result.evidence)}

        if result.status == "denied":
            self._repository.mark_failed(
                run_id,
                error_json={
                    "error": "authorization denied",
                    "message": "denied actor cannot retrieve policy evidence",
                    "retry_count": 0,
                },
                retry_count=0,
                max_retries=self._max_retries,
                events=[_event("run.failed", details={"error": "authorization denied"})],
            )
            return {"run_id": run_id, "status": "denied"}

        retry_count = int(row["retry_count"]) + 1
        if retry_count > self._max_retries:
            self._repository.mark_failed(
                run_id,
                error_json={
                    "error": "pipeline failed",
                    "message": result.error or "evidence pipeline terminated abnormally",
                    "retry_count": retry_count,
                },
                retry_count=retry_count,
                max_retries=self._max_retries,
                events=[_event("run.failed", details={"error": "pipeline failed"})],
            )
            return {"run_id": run_id, "status": "dead_letter", "error": result.error or "pipeline failed"}

        self._repository.requeue_retry(run_id, retry_count)
        return {"run_id": run_id, "status": "retry", "retry_count": retry_count}
