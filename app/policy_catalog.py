"""SQLite-backed policy version catalog and deterministic event timeline."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any


class PolicyCatalog:
    """Persist published policy snapshots and append-only publication events."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_versions (
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    PRIMARY KEY (policy_id, version)
                );
                CREATE TABLE IF NOT EXISTS policy_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS ingestion_sync_outcomes (
                    policy_id TEXT PRIMARY KEY,
                    outcome TEXT NOT NULL CHECK (
                        outcome IN ('added', 'changed', 'unchanged', 'removed') OR outcome LIKE 'error:%'
                    ),
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingestion_file_status (
                    tenant_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('indexed', 'error')),
                    message TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, document_id)
                );
                CREATE TABLE IF NOT EXISTS source_watermarks (
                    policy_id TEXT PRIMARY KEY,
                    observed_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    last_success_at TEXT,
                    last_failure_at TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open an immediate transaction for coordinated catalog/index writes."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def publish(
        self,
        record: dict[str, Any],
        *,
        occurred_at: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        required = ("id", "version", "department_id", "content_hash", "status", "effective_from")
        if any(not isinstance(record.get(field), str) or not record[field].strip() for field in required):
            raise ValueError("policy record is missing required non-empty fields")
        if record["status"] != "active":
            raise ValueError("only active policy versions can be published")
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be a non-empty timestamp")
        if connection is None:
            with self.transaction() as db:
                self.publish(record, occurred_at=occurred_at, connection=db)
            return

        try:
            connection.execute(
                "INSERT INTO policy_versions VALUES (?, ?, ?, ?, ?)",
                (record["id"], record["version"], record["department_id"],
                 record["content_hash"], json.dumps(record, sort_keys=True, separators=(",", ":"))),
            )
            connection.execute(
                "INSERT INTO policy_events (event_type, policy_id, version, department_id, occurred_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("published", record["id"], record["version"], record["department_id"], occurred_at, None),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"policy version already exists: {record['id']}@{record['version']}") from error

    def record_file_status(
        self, tenant_id: str, document_id: str, *, status: str,
        message: str, occurred_at: str,
    ) -> None:
        if status not in {"indexed", "error"} or not tenant_id or not document_id:
            raise ValueError("invalid per-file ingestion status")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO ingestion_file_status
                    (tenant_id, document_id, status, message, occurred_at) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, document_id) DO UPDATE SET
                        status=excluded.status, message=excluded.message, occurred_at=excluded.occurred_at""",
                    (tenant_id, document_id, status, message[:160], occurred_at),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def file_status(self, tenant_id: str) -> list[dict[str, str]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_id, status, message, occurred_at FROM ingestion_file_status WHERE tenant_id=? ORDER BY document_id",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def sync_outcomes(
        self, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, str]:
        if connection is None:
            with closing(self._connect()) as db:
                return self.sync_outcomes(connection=db)
        rows = connection.execute(
            "SELECT policy_id, outcome FROM ingestion_sync_outcomes"
        ).fetchall()
        return {row["policy_id"]: row["outcome"] for row in rows}

    def record_sync_outcomes(
        self,
        outcomes: dict[str, str],
        *,
        occurred_at: str,
        connection: sqlite3.Connection,
    ) -> None:
        if not connection.in_transaction:
            raise ValueError("record_sync_outcomes requires an active transaction")
        connection.executemany(
            """INSERT INTO ingestion_sync_outcomes(policy_id, outcome, recorded_at)
            VALUES (?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                outcome=excluded.outcome, recorded_at=excluded.recorded_at""",
            [(policy_id, outcome, occurred_at) for policy_id, outcome in outcomes.items()],
        )

    def record_source_watermark(
        self,
        policy_id: str,
        observed_hash: str,
        observed_at: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if connection is None:
            with closing(self._connect()) as db:
                return self.record_source_watermark(policy_id, observed_hash, observed_at, connection=db)
        connection.execute(
            """INSERT INTO source_watermarks(policy_id, observed_hash, observed_at, last_success_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(policy_id) DO UPDATE SET
                observed_hash=excluded.observed_hash,
                observed_at=excluded.observed_at,
                last_success_at=excluded.last_success_at""",
            (policy_id, observed_hash, observed_at, observed_at),
        )

    def record_source_failure(
        self, policy_id: str, failed_at: str, connection: sqlite3.Connection | None = None
    ) -> None:
        if connection is None:
            db = self._connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute(
                        "UPDATE source_watermarks SET last_failure_at=?, failed_attempts=failed_attempts+1 "
                        "WHERE policy_id=?",
                        (failed_at, policy_id),
                    )
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            finally:
                db.close()
            return
        connection.execute(
            "UPDATE source_watermarks SET last_failure_at=?, failed_attempts=failed_attempts+1 "
            "WHERE policy_id=?",
            (failed_at, policy_id),
        )

    def source_watch_status(self, *, connection: sqlite3.Connection | None = None) -> dict[str, dict[str, str]]:
        if connection is None:
            with closing(self._connect()) as db:
                return self.source_watch_status(connection=db)
        rows = connection.execute(
            "SELECT policy_id, observed_hash, observed_at, last_success_at, last_failure_at, failed_attempts "
            "FROM source_watermarks ORDER BY policy_id"
        ).fetchall()
        return {
            row["policy_id"]: {
                "observed_hash": row["observed_hash"],
                "observed_at": row["observed_at"],
                "last_success_at": row["last_success_at"],
                "last_failure_at": row["last_failure_at"],
                "failed_attempts": str(row["failed_attempts"]),
            }
            for row in rows
        }

    def current(
        self, policy_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        if connection is None:
            with closing(self._connect()) as db:
                return self.current(policy_id, connection=db)
        row = connection.execute(
            "SELECT record_json FROM policy_versions WHERE policy_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (policy_id,)
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def current_records(
        self, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, dict[str, Any]]:
        if connection is None:
            with closing(self._connect()) as db:
                return self.current_records(connection=db)
        rows = connection.execute(
            "SELECT record_json FROM policy_versions "
            "WHERE rowid IN (SELECT MAX(rowid) FROM policy_versions GROUP BY policy_id)"
        ).fetchall()
        records = [json.loads(row["record_json"]) for row in rows]
        return {record["id"]: record for record in records}

    def version(
        self,
        policy_id: str,
        version: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        if connection is None:
            with closing(self._connect()) as db:
                return self.version(policy_id, version, connection=db)
        row = connection.execute(
            "SELECT record_json FROM policy_versions WHERE policy_id=? AND version=?",
            (policy_id, version),
        ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def versions(
        self, policy_id: str, *, connection: sqlite3.Connection | None = None
    ) -> list[dict[str, Any]]:
        if connection is None:
            with closing(self._connect()) as db:
                return self.versions(policy_id, connection=db)
        rows = connection.execute(
            "SELECT record_json FROM policy_versions WHERE policy_id=? ORDER BY rowid",
            (policy_id,),
        ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def supersede(
        self,
        policy_id: str,
        version: str,
        *,
        occurred_at: str,
        connection: sqlite3.Connection,
    ) -> None:
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be a non-empty timestamp")
        record = self.version(policy_id, version, connection=connection)
        if record is None:
            raise ValueError(f"unknown policy version: {policy_id}@{version}")
        if record["status"] != "active":
            raise ValueError(f"policy version is not active: {policy_id}@{version}")
        record["status"] = "superseded"
        connection.execute(
            "UPDATE policy_versions SET record_json=? WHERE policy_id=? AND version=?",
            (json.dumps(record, sort_keys=True, separators=(",", ":")), policy_id, version),
        )
        connection.execute(
            "INSERT INTO policy_events (event_type, policy_id, version, department_id, occurred_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("superseded", policy_id, version, record["department_id"], occurred_at, "new source version published"),
        )

    def withdraw(
        self,
        policy_id: str,
        *,
        occurred_at: str,
        reason: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Append a withdrawal event and mark the current snapshot withdrawn."""
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be a non-empty timestamp")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        if connection is None:
            with self.transaction() as db:
                self.withdraw(policy_id, occurred_at=occurred_at, reason=reason, connection=db)
            return

        row = connection.execute(
            "SELECT version, department_id, record_json FROM policy_versions "
            "WHERE policy_id = ? ORDER BY rowid DESC LIMIT 1", (policy_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown policy: {policy_id}")
        record = json.loads(row["record_json"])
        if record["status"] != "active":
            raise ValueError(f"policy is not active: {policy_id}@{row['version']}")
        record["status"] = "withdrawn"
        connection.execute(
            "UPDATE policy_versions SET record_json = ? WHERE policy_id = ? AND version = ?",
            (json.dumps(record, sort_keys=True, separators=(",", ":")), policy_id, row["version"]),
        )
        connection.execute(
            "INSERT INTO policy_events (event_type, policy_id, version, department_id, occurred_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("withdrawn", policy_id, row["version"], row["department_id"], occurred_at, reason),
        )

    def activate_due_changes(self, *, as_of: str) -> int:
        """Expire active versions whose inclusive effective period has ended.

        The caller supplies a deterministic ISO calendar date; no wall-clock
        access is hidden in catalog state transitions. Returns changed count.
        """
        if not isinstance(as_of, str) or not as_of.strip():
            raise ValueError("as_of must be a non-empty ISO date")
        try:
            from datetime import date
            evaluated = date.fromisoformat(as_of)
        except ValueError as error:
            raise ValueError("as_of must be an ISO calendar date") from error

        def expire(connection: sqlite3.Connection) -> int:
            changed = 0
            rows = connection.execute(
                "SELECT policy_id, version, department_id, record_json FROM policy_versions "
                "WHERE rowid IN (SELECT MAX(rowid) FROM policy_versions GROUP BY policy_id)"
            ).fetchall()
            for row in rows:
                record = json.loads(row["record_json"])
                effective_to = record.get("effective_to")
                if record["status"] != "active" or effective_to is None:
                    continue
                try:
                    expiry = date.fromisoformat(effective_to)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"invalid effective_to for {row['policy_id']}: {effective_to}") from error
                if expiry >= evaluated:
                    continue
                record["status"] = "expired"
                connection.execute(
                    "UPDATE policy_versions SET record_json = ? WHERE policy_id = ? AND version = ?",
                    (json.dumps(record, sort_keys=True, separators=(",", ":")), row["policy_id"], row["version"]),
                )
                connection.execute(
                    "INSERT INTO policy_events (event_type, policy_id, version, department_id, occurred_at, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("expired", row["policy_id"], row["version"], row["department_id"], as_of, "effective period ended"),
                )
                changed += 1
            return changed

        with self.transaction() as connection:
            return expire(connection)

    def timeline(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, policy_id, version, department_id, occurred_at, reason "
                "FROM policy_events ORDER BY sequence"
            ).fetchall()
        return [dict(row) for row in rows]
