"""Durable async evidence runs: outbox rows, run state, cancellation, and events.

Every enqueue is a single row in the shared SQLite database (the outbox) plus an
append-only event row. Workers claim exactly one queued row at a time, process it
through the async LangGraph evidence pipeline, and mark the outcome completed,
failed, cancelled or dead_letter. The in-place status change means a queue
restart never loses a run and never turns a completed outcome into something
else; a revoked actor fails closed before any retrieval text is produced.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

import hashlib

from app.retrieval import PolicyEvidence, SQLiteEvidenceRetriever

# Shared outbox namespace inside the policy catalog database (same file).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    actor_subject_id TEXT,
    query TEXT NOT NULL,
    departments_json TEXT NOT NULL,
    policy_ids_json TEXT NOT NULL DEFAULT 'null',
    as_of TEXT NOT NULL,
    evidence_mode TEXT NOT NULL,
    answer_cache TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    result_json TEXT,
    error_json TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 10,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    last_event_at TEXT,
    claimed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_evidence_runs_status
    ON evidence_runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_runs_tenant
    ON evidence_runs(tenant_id, status);
CREATE TABLE IF NOT EXISTS evidence_run_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor_subject_id TEXT,
    details_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES evidence_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_evidence_run_events_run
    ON evidence_run_events(run_id, sequence);
"""

_VALID_STATUSES = frozenset(
    {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "dead_letter",
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "dead_letter",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EvidenceRunEvent(BaseModel):
    """Append-only audit event for one evidence run."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=128)
    occurred_at: str
    actor_subject_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class EvidenceRunState(TypedDict, total=False):
    """TypedGraph state for one async evidence run (checkpoint-visible).

    Internal workflow keys (``_scope``, ``_retriever``, ``code``) are carried in
    the same dict at runtime; they are optional for static typing.
    """

    run_id: str
    tenant_id: str
    actor_subject_id: str | None
    query: str
    departments: tuple[str, ...]
    as_of: str
    evidence_mode: str
    answer_cache: str
    code: str
    evidence: tuple[dict[str, Any], ...]
    error: str | None
    # Runtime-only channels; present while the async evidence run executes and
    # carried through checkpoint/resume by the SQLAlchemy-backed graph.
    _scope: Callable[..., set[str]] | None
    _retriever: SQLiteEvidenceRetriever | None
    allowed_policy_ids: set[str]


class EvidenceRun(BaseModel):
    """Read-only report of a single evidence run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    actor_subject_id: str | None
    query: str
    departments: tuple[str, ...]
    as_of: str
    evidence_mode: str
    answer_cache: str
    status: str
    result: tuple[PolicyEvidence, ...] = ()
    error: str | None = None
    retry_count: int = 0
    max_retries: int = 10
    idempotency_key: str | None = None
    events: tuple[EvidenceRunEvent, ...] = ()
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None


class EvidenceRunResult(BaseModel):
    """Outcome produced by the async evidence pipeline for one run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    evidence: tuple[PolicyEvidence, ...]
    error: str | None = None


class EvidenceRunRepository:
    """SQLite-backed durable outbox and run state for async evidence runs."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # ------------------------------------------------------------------ create
    def create_run(
        self,
        *,
        tenant_id: str,
        actor_subject_id: str | None,
        query: str,
        departments: Iterable[str],
        as_of: str,
        evidence_mode: str,
        answer_cache: str,
        idempotency_key: str | None = None,
        max_retries: int = 10,
        now: str | None = None,
    ) -> str:
        """Insert one outbox row. Idempotent on an explicit or payload-derived key."""
        now = now or _now_iso()
        if idempotency_key is None:
            idempotency_key = self._payload_idempotency_key(
                tenant_id=tenant_id,
                actor_subject_id=actor_subject_id,
                query=query,
                departments=sorted(departments),
                as_of=as_of,
                evidence_mode=evidence_mode,
                answer_cache=answer_cache,
            )
        existing = self._find_by_idempotency(idempotency_key)
        if existing is not None:
            return existing["run_id"]
        if evidence_mode not in {"freshness_preferred", "ttl_snapshot", "critical"}:
            raise ValueError("evidence_mode must be freshness_preferred, ttl_snapshot or critical")
        if answer_cache not in {"off", "exact"}:
            raise ValueError("answer_cache must be off or exact")
        run_id = uuid4().hex
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO evidence_runs
                        (run_id, tenant_id, actor_subject_id, query, departments_json,
                         policy_ids_json, as_of, evidence_mode, answer_cache,
                         idempotency_key, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        run_id,
                        tenant_id,
                        actor_subject_id,
                        query,
                        json.dumps(list(departments)),
                        "null",
                        as_of,
                        evidence_mode,
                        answer_cache,
                        idempotency_key,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO evidence_run_events
                        (run_id, event_type, occurred_at, actor_subject_id, details_json)
                    VALUES (?, 'run.created', ?, ?, ?)
                    """,
                    (run_id, now, actor_subject_id,
                     json.dumps({"run_id": run_id, "status": "queued"})),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return run_id

    # ------------------------------------------------------------- claim
    @staticmethod
    def _payload_idempotency_key(
        *,
        tenant_id: str,
        actor_subject_id: str | None,
        query: str,
        departments: list[str],
        as_of: str,
        evidence_mode: str,
        answer_cache: str,
    ) -> str:
        """Derive a stable payload hash so identical submissions reuse the run."""
        payload = json.dumps(
            {
                "tenant_id": tenant_id,
                "actor_subject_id": actor_subject_id,
                "query": query,
                "as_of": as_of,
                "evidence_mode": evidence_mode,
                "answer_cache": answer_cache,
                "departments": departments,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "payload:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def claim(self) -> sqlite3.Row | None:
        """Claim exactly one queued run. Short write lock; no long-lived lock."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                pending = connection.execute(
                    """
                    SELECT run_id FROM evidence_runs
                    WHERE status = 'queued'
                    ORDER BY created_at, run_id
                    LIMIT 1
                    """
                ).fetchone()
                if pending is None:
                    connection.commit()
                    return None
                run_id = pending["run_id"]
                connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'running', started_at = ?, claimed_at = ?
                    WHERE run_id = ?
                    """,
                    (_now_iso(), _now_iso(), run_id),
                )
                connection.commit()
                return connection.execute(
                    "SELECT * FROM evidence_runs WHERE status = 'running' "
                    "ORDER BY started_at DESC, run_id DESC LIMIT 1"
                ).fetchone()
            except BaseException:
                connection.rollback()
                raise

    # ------------------------------------------------------------- outcome
    def mark_completed(
        self,
        run_id: str,
        result_json: str,
        *,
        events: list["EvidenceRunEvent"],
        now: str | None = None,
    ) -> bool:
        now = now or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'completed', result_json = ?, completed_at = ?,
                        last_event_at = ?
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (result_json, now, now, run_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                self._append_events(connection, run_id, events)
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def mark_failed(
        self,
        run_id: str,
        *,
        error_json: dict[str, Any],
        retry_count: int,
        max_retries: int,
        events: list["EvidenceRunEvent"],
        now: str | None = None,
    ) -> bool:
        now = now or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'failed', error_json = ?, retry_count = ?, max_retries = ?,
                        completed_at = ?, last_event_at = ?
                    WHERE run_id = ? AND status = 'running'
                    """,
                    (json.dumps(error_json, sort_keys=True), retry_count, max_retries,
                     now, now, run_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                self._append_events(connection, run_id, events)
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def mark_dead_letter(
        self,
        run_id: str,
        *,
        error_json: dict[str, Any],
        events: list["EvidenceRunEvent"],
        now: str | None = None,
    ) -> bool:
        now = now or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'dead_letter', error_json = ?, completed_at = ?,
                        last_event_at = ?
                    WHERE run_id = ? AND status = 'failed'
                    """,
                    (json.dumps(error_json, sort_keys=True), now, now, run_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                self._append_events(connection, run_id, events)
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def mark_cancelled(self, run_id: str, *, events: list["EvidenceRunEvent"], now: str | None = None) -> bool:
        now = now or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'cancelled', cancelled_at = ?, last_event_at = ?
                    WHERE run_id = ? AND status IN ('queued', 'running')
                    """,
                    (now, now, run_id),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                self._append_events(connection, run_id, events)
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def requeue_retry(self, run_id: str, retry_count: int) -> bool:
        """Return a failed/running run to queued so the bounded worker retries it."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE evidence_runs
                    SET status = 'queued', started_at = NULL, completed_at = NULL,
                        error_json = NULL, retry_count = ?, last_event_at = NULL
                    WHERE run_id = ? AND status IN ('failed', 'running')
                    """,
                    (retry_count, run_id),
                )
                connection.commit()
                return cursor.rowcount == 1
            except BaseException:
                connection.rollback()
                raise

    def requeue_stale(self, *, stale_after_seconds: float = 0.0) -> int:
        """CRASH RECOVERY: requeue runs left 'running' with no outcome.

        Test-only helper that models a worker loss without a persisted outcome.
        """
        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if stale_after_seconds > 0:
                    cursor = connection.execute(
                        """
                        UPDATE evidence_runs
                        SET status = 'queued', started_at = NULL, claimed_at = NULL
                        WHERE status = 'running'
                          AND julianday(?) - julianday(last_event_at) > ?
                        """,
                        (now, stale_after_seconds),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE evidence_runs
                        SET status = 'queued', started_at = NULL, claimed_at = NULL
                        WHERE status = 'running'
                        """,
                    )
                connection.commit()
                return cursor.rowcount
            except BaseException:
                connection.rollback()
                raise

    def is_terminal(self, run_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM evidence_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return bool(row) and row["status"] in _TERMINAL_STATUSES

    def is_cancelled(self, run_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM evidence_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return bool(row) and row["status"] == "cancelled"

    # ------------------------------------------------------------- list
    def list_runs(
        self, tenant_id: str, *, limit: int = 20, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Cursor-paginated recent runs for a tenant (run_id cursor, newest first)."""
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM evidence_runs
                WHERE tenant_id = ? AND run_id > ?
                ORDER BY created_at DESC, run_id DESC
                LIMIT ?
                """,
                (tenant_id, cursor or "", limit),
            ).fetchall()
        next_cursor = rows[-1]["run_id"] if len(rows) == limit else None
        return [dict(row) for row in rows], next_cursor

    # --------------------------------------------------------------- read
    def status(self, run_id: str) -> EvidenceRun:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM evidence_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            events = [
                EvidenceRunEvent.model_validate(item)
                for item in self._event_rows(connection, run_id)
            ]
        return EvidenceRun(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            actor_subject_id=row["actor_subject_id"],
            query=row["query"],
            departments=tuple(json.loads(row["departments_json"]) or ()),
            as_of=row["as_of"],
            evidence_mode=row["evidence_mode"],
            answer_cache=row["answer_cache"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            result=tuple(self._result_to_evidence(row["result_json"]) or ()),
            error=(json.loads(row["error_json"] or "{}").get("error")
                   if row["error_json"] else None),
            retry_count=int(row["retry_count"]),
            max_retries=int(row["max_retries"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
            events=tuple(events),
        )

    def _event_rows(self, connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "event_type": item["event_type"],
                "occurred_at": item["occurred_at"],
                "actor_subject_id": item["actor_subject_id"],
                "details": json.loads(item["details_json"] or "{}"),
            }
            for item in connection.execute(
                "SELECT * FROM evidence_run_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        ]

    @staticmethod
    def _result_to_evidence(result_json: str | None) -> list[PolicyEvidence]:
        if not result_json:
            return []
        try:
            records = json.loads(result_json)
        except ValueError:
            return []
        return [PolicyEvidence(**item) for item in records]

    def _append_events(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        events: list["EvidenceRunEvent"],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO evidence_run_events
                (run_id, event_type, occurred_at, actor_subject_id, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    event.event_type,
                    event.occurred_at,
                    event.actor_subject_id,
                    json.dumps(event.details, sort_keys=True),
                )
                for event in events
            ],
        )

    def _find_by_idempotency(self, idempotency_key: str) -> sqlite3.Row | None:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT * FROM evidence_runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()


def evidence_to_dicts(evidence: Iterable[PolicyEvidence]) -> tuple[dict[str, Any], ...]:
    """JSON-serialisable view of evidence for LangGraph checkpointer channels."""
    return tuple(item.to_dict() for item in evidence)


def evidence_from_dicts(records: Iterable[dict[str, Any]]) -> tuple[PolicyEvidence, ...]:
    return tuple(PolicyEvidence(**record) for record in records)
