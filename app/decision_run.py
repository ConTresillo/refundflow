"""Durable, auditable decision runs for the bounded simulated decision workflow.

A decision run executes the bounded graph (``verify_context`` -> ``propose`` ->
``await_approval`` -> ``record_decision``) against a durable SQLite
checkpointer and records an append-only audit trail. The workflow interrupts
once for a single human/agent approval; the approval value is the sole
terminal input, and the outcome is recorded exactly once.

This module owns only persistence and run metadata. The workflow itself is
built in ``app.decision_graph``; the HTTP surface lives in
``app.decision_run_routes``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# Shared outbox namespace inside the policy catalog database (same file).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    actor_subject_id TEXT NOT NULL,
    question TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    as_of TEXT NOT NULL,
    evidence_mode TEXT NOT NULL,
    answer_cache TEXT NOT NULL,
    status TEXT NOT NULL,
    proposal TEXT,
    decision TEXT,
    code TEXT NOT NULL,
    error TEXT,
    idempotency_key TEXT,
    command_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cancelled_at TEXT,
    last_event_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_decision_runs_tenant_status
    ON decision_runs(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_decision_runs_run_id
    ON decision_runs(run_id);
CREATE TABLE IF NOT EXISTS decision_run_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    actor_subject_id TEXT,
    details_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES decision_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_decision_run_events_run
    ON decision_run_events(run_id, sequence);
-- Append-only protection for the audit trail: any UPDATE or DELETE must fail
-- closed. Business mutations (create/approve/cancel) write through this table
-- only via the repository's transactional INSERT paths, never by direct
-- UPDATE/DELETE. Triggers are idempotently created here; they survive database
-- rebuilds but are application-level, not WORM assurance.
CREATE TRIGGER IF NOT EXISTS trg_decision_run_events_no_update
BEFORE UPDATE ON decision_run_events
FOR EACH ROW
BEGIN
    SELECT RAISE(FAIL, 'decision_run_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_decision_run_events_no_delete
BEFORE DELETE ON decision_run_events
FOR EACH ROW
BEGIN
    SELECT RAISE(FAIL, 'decision_run_events is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_decision_runs_command_id_required
BEFORE UPDATE OF command_id ON decision_runs
FOR EACH ROW
WHEN OLD.command_id IS NOT NULL AND OLD.command_id != NEW.command_id
BEGIN
    SELECT RAISE(FAIL, 'command_id cannot be reassigned');
END;
"""


_VALID_STATUSES = frozenset(
    {
        "interrupted",
        "completed",
        "denied",
        "failed",
        "cancelled",
        "dead_letter",
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "denied",
        "failed",
        "cancelled",
        "dead_letter",
    }
)

_APPROVAL_DECISIONS = frozenset({"approve", "deny"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DecisionRunEvent(BaseModel):
    """Append-only audit event for one decision run."""

    model_config = ConfigDict(extra="forbid")

    event_type: str = Field(min_length=1, max_length=128)
    occurred_at: str
    actor_subject_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DecisionRun(BaseModel):
    """Read-only report of one decision run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    tenant_id: str
    actor_subject_id: str | None
    question: str
    evidence: tuple[dict[str, Any], ...]
    as_of: str
    evidence_mode: str
    answer_cache: str
    status: str
    code: str
    proposal: str | None = None
    decision: str | None = None
    error: str | None = None
    command_id: str | None = None
    events: tuple[DecisionRunEvent, ...] = ()
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        data["evidence"] = list(data["evidence"])
        data["events"] = list(data["events"])
        return data


class DecisionRunRepository:
    """SQLite-backed durable outbox and run state for decision runs."""

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

    @staticmethod
    def _payload_idempotency_key(
        tenant_id: str,
        actor_subject_id: str | None,
        question: str,
        evidence: Iterable[dict[str, Any]],
        as_of: str,
    ) -> str:
        payload = {
            "tenant_id": tenant_id,
            "actor_subject_id": actor_subject_id,
            "question": question,
            "evidence": sorted(
                json.dumps(item, sort_keys=True) for item in sorted(evidence, key=lambda item: str(item))
            ),
            "as_of": as_of,
        }
        return "payload:" + hashlib.sha1(
            json.dumps(payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

    # -------------------------------------------------------------- create
    def create_run(
        self,
        *,
        tenant_id: str,
        actor_subject_id: str,
        question: str,
        evidence: Iterable[dict[str, Any]],
        as_of: str,
        evidence_mode: str,
        answer_cache: str,
        idempotency_key: str | None = None,
        command_id: str | None = None,
        now: str | None = None,
    ) -> str:
        """Insert one outbox row and return its run id.

        ``command_id`` is an optional, per-command idempotency token. When a
        run is created with a ``command_id`` that already exists on a run in
        the same tenant, the existing run id is returned instead of creating a
        duplicate (dedupe by command). The token is not a user-supplied
        credential: it is an application-level command identifier.
        """
        now = now or _now_iso()

        if idempotency_key is None:
            idempotency_key = self._payload_idempotency_key(
                tenant_id=tenant_id,
                actor_subject_id=actor_subject_id,
                question=question,
                evidence=evidence,
                as_of=as_of,
            )

        existing = self._find_by_idempotency(idempotency_key)
        if existing is not None:
            return existing["run_id"]

        if evidence_mode not in {"freshness_preferred", "ttl_snapshot", "critical"}:
            raise ValueError("evidence_mode is not a recognized evidence mode")
        if answer_cache not in {"off", "exact"}:
            raise ValueError("answer_cache must be off or exact")

        run_id = uuid4().hex
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO decision_runs
                        (run_id, tenant_id, actor_subject_id, question, evidence_json,
                         as_of, evidence_mode, answer_cache, idempotency_key,
                         command_id, status, code, created_at, last_event_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'interrupted', 'interrupted', ?, ?)
                    """,
                    (
                        run_id,
                        tenant_id,
                        actor_subject_id,
                        question,
                        json.dumps(
                            [
                                dict(item)
                                for item in sorted(evidence, key=lambda item: str(item))
                            ],
                            sort_keys=True,
                        ),
                        as_of,
                        evidence_mode,
                        answer_cache,
                        idempotency_key,
                        command_id,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO decision_run_events
                        (run_id, event_type, occurred_at, actor_subject_id, details_json)
                    VALUES (?, 'run.created', ?, ?, ?)
                    """,
                    (run_id, now, actor_subject_id,
                     json.dumps({"run_id": run_id, "status": "interrupted", "command_id": command_id})),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return run_id

    # --------------------------------------------------------------- status
    def status(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM decision_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_type, occurred_at, actor_subject_id, details_json "
                "FROM decision_run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        actor_subject_id: str | None = None,
        details: dict[str, Any] | None = None,
        command_id: str | None = None,
    ) -> None:
        now = _now_iso()
        with closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO decision_run_events (run_id, event_type, occurred_at, "
                "actor_subject_id, details_json) VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_type,
                    now,
                    actor_subject_id,
                    json.dumps(
                        {"run_id": run_id, "event_type": event_type,
                         "command_id": command_id,
                         **(details or {})},
                        sort_keys=True,
                    ),
                ),
            )
            connection.execute(
                "UPDATE decision_runs SET last_event_at=? WHERE run_id=?",
                (now, run_id),
            )
            connection.commit()

    # -------------------------------------------------------------- approve
    def record_graph_output(
        self,
        run_id: str,
        *,
        proposal: str | None = None,
        code: str | None = None,
        error: str | None = None,
        command_id: str | None = None,
        occurred_at: str | None = None,
    ) -> None:
        """Record the graph's latest output without changing the terminal outcome.

        ``command_id`` links this graph output to a client-issued command
        (for example, one approval attempt). Passing a command_id that does
        not belong to this run is rejected; it is an application-level field, not
        a user-supplied credential.
        """
        occurred_at = occurred_at or _now_iso()
        if command_id is not None:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT command_id FROM decision_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None or row["command_id"] not in (None, command_id):
                    raise LookupError(f"command_id {command_id!r} does not belong to run {run_id}")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updates = []
                params = []
                if proposal is not None:
                    updates.append("proposal=?")
                    params.append(proposal)
                if code is not None:
                    updates.append("code=?")
                    params.append(code)
                if error is not None:
                    updates.append("error=?")
                    params.append(error)
                if updates:
                    connection.execute(
                        f"UPDATE decision_runs SET {', '.join(updates)}, last_event_at=? WHERE run_id=?",
                        [*params, occurred_at, run_id],
                    )
                    connection.execute(
                        """
                        INSERT INTO decision_run_events
                            (run_id, event_type, occurred_at, actor_subject_id, details_json)
                        VALUES (?, 'workflow.output', ?, ?, ?)
                        """,
                        (
                            run_id,
                            occurred_at,
                            None,
                            json.dumps(
                                {
                                    "run_id": run_id,
                                    "proposal": proposal,
                                    "code": code,
                                    "error": error,
                                    "command_id": command_id,
                                }
                            ),
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
    def approve_run(
        self,
        run_id: str,
        decision: str,
        actor_subject_id: str,
        command_id: str | None = None,
        occurred_at: str | None = None,
    ) -> str:
        """Record the single approval and mark the terminal outcome.

        Fails closed for any decision value that is not exactly one of the
        allowlisted options and for a run that is already terminal. When a
        ``command_id`` is supplied, reapplying the same command returns the
        recorded decision (idempotent); a conflicting command_id on an already
        recorded run is rejected. ``command_id`` is an application-level command
        token, never a user-supplied credential.
        """
        if decision not in _APPROVAL_DECISIONS:
            raise ValueError(f"decision must be approve or deny, got {decision!r}")
        occurred_at = occurred_at or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, decision, code, command_id FROM decision_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"unknown decision run: {run_id}")
                if command_id is not None:
                    stored_command_id = row["command_id"]
                    if stored_command_id is not None and stored_command_id != command_id:
                        raise LookupError(
                            f"command_id {command_id!r} conflicts with existing "
                            f"command {stored_command_id!r} on run {run_id}"
                        )
                    if stored_command_id == command_id:
                        if row["decision"] == decision:
                            # Idempotent: same command and same decision, return the
                            # recorded outcome without re-recording.
                            connection.commit()
                            return row["decision"]
                        if row["decision"] is not None:
                            # Same command_id already resolved to a different decision:
                            # fail closed so a conflicting command never overwrites the
                            # recorded outcome.
                            raise LookupError(
                                f"decision already recorded as {row['decision']!r}; "
                                f"cannot record {decision!r}"
                            )
                    if stored_command_id != command_id:
                        # New command for this run: record it.
                        connection.execute(
                            "UPDATE decision_runs SET command_id=? WHERE run_id=?",
                            (command_id, run_id),
                        )
                if row["status"] in _TERMINAL_STATUSES:
                    raise LookupError(f"decision run is already terminal: {run_id}")
                if row["decision"] is not None and row["decision"] != decision:
                    raise LookupError(
                        f"decision already recorded as {row['decision']!r}; "
                        f"cannot record {decision!r}"
                    )

                outcome_code = "completed" if decision == "approve" else "denied"
                connection.execute(
                    """
                    UPDATE decision_runs
                    SET status=?, decision=?, code=?, completed_at=?, error=NULL
                    WHERE run_id=?
                    """,
                    (
                        "completed" if decision == "approve" else "denied",
                        decision,
                        outcome_code,
                        occurred_at,
                        run_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO decision_run_events
                        (run_id, event_type, occurred_at, actor_subject_id, details_json)
                    VALUES (?, 'decision.recorded', ?, ?, ?)
                    """,
                    (
                        run_id,
                        occurred_at,
                        actor_subject_id,
                        json.dumps(
                            {
                                "run_id": run_id,
                                "decision": decision,
                                "code": outcome_code,
                                "command_id": command_id,
                            }
                        ),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return outcome_code

    def cancel_run(
        self,
        run_id: str,
        actor_subject_id: str,
        command_id: str | None = None,
        occurred_at: str | None = None,
    ) -> bool:
        """Mark a queued or interrupted run cancelled (fail-closed)."""
        occurred_at = occurred_at or _now_iso()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, command_id FROM decision_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"unknown decision run: {run_id}")
                if row["status"] in _TERMINAL_STATUSES:
                    return False
                if command_id is not None:
                    if row["command_id"] is not None and row["command_id"] != command_id:
                        raise LookupError(
                            f"command_id {command_id!r} conflicts with existing "
                            f"command {row['command_id']!r} on run {run_id}"
                        )
                    if row["command_id"] == command_id:
                        connection.commit()
                        return False
                    connection.execute(
                        "UPDATE decision_runs SET command_id=? WHERE run_id=?",
                        (command_id, run_id),
                    )
                connection.execute(
                    """
                    UPDATE decision_runs
                    SET status='cancelled', cancelled_at=?, error=NULL
                    WHERE run_id=?
                    """,
                    (occurred_at, run_id),
                )
                connection.execute(
                    """
                    INSERT INTO decision_run_events
                        (run_id, event_type, occurred_at, actor_subject_id, details_json)
                    VALUES (?, 'run.cancelled', ?, ?, ?)
                    """,
                    (
                        run_id,
                        occurred_at,
                        actor_subject_id,
                        json.dumps(
                            {"run_id": run_id, "command_id": command_id, "cancelled": True}
                        ),
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return True

    def list_runs(
        self,
        tenant_id: str,
        *,
        limit: int = 20,
    ) -> list[DecisionRun]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM decision_runs WHERE tenant_id=? "
                "ORDER BY created_at DESC, run_id DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
            return [self._row_to_decision_run(row) for row in rows]

    # ------------------------------------------------------------- helpers
    def _find_by_idempotency(self, idempotency_key: str) -> sqlite3.Row | None:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT * FROM decision_runs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()

    @staticmethod
    def _row_to_decision_run(row: sqlite3.Row, *, events: list[dict[str, Any]] | None = None) -> DecisionRun:
        evidence = json.loads(row["evidence_json"])
        event_objects = tuple(
            DecisionRunEvent(
                event_type=ev["event_type"],
                occurred_at=ev["occurred_at"],
                actor_subject_id=ev["actor_subject_id"],
                details=json.loads(ev["details_json"]),
            )
            for ev in (events or ())
        )
        return DecisionRun(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            actor_subject_id=row["actor_subject_id"],
            question=row["question"],
            evidence=evidence,
            as_of=row["as_of"],
            evidence_mode=row["evidence_mode"],
            answer_cache=row["answer_cache"],
            status=row["status"],
            code=row["code"],
            proposal=row["proposal"],
            decision=row["decision"],
            error=row["error"],
            command_id=row["command_id"],
            events=event_objects,
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            cancelled_at=row["cancelled_at"],
        )
