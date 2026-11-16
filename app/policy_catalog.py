"""SQLite-backed policy version catalog and deterministic event timeline."""

from __future__ import annotations

import json
import sqlite3
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
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def publish(self, record: dict[str, Any], *, occurred_at: str) -> None:
        required = ("id", "version", "department_id", "content_hash", "status", "effective_from")
        if any(not isinstance(record.get(field), str) or not record[field].strip() for field in required):
            raise ValueError("policy record is missing required non-empty fields")
        if record["status"] != "active":
            raise ValueError("only active policy versions can be published")
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be a non-empty timestamp")

        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO policy_versions VALUES (?, ?, ?, ?, ?)",
                    (record["id"], record["version"], record["department_id"], record["content_hash"],
                     json.dumps(record, sort_keys=True, separators=(",", ":"))),
                )
                connection.execute(
                    "INSERT INTO policy_events (event_type, policy_id, version, department_id, occurred_at, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("published", record["id"], record["version"], record["department_id"], occurred_at, None),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"policy version already exists: {record['id']}@{record['version']}") from error

    def current(self, policy_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM policy_versions WHERE policy_id = ? "
                "ORDER BY rowid DESC LIMIT 1", (policy_id,)
            ).fetchone()
        return json.loads(row["record_json"]) if row else None

    def withdraw(self, policy_id: str, *, occurred_at: str, reason: str) -> None:
        """Append a withdrawal event and mark the current snapshot withdrawn."""
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be a non-empty timestamp")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")

        with self._connect() as connection:
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

        changed = 0
        with self._connect() as connection:
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

    def timeline(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_type, policy_id, version, department_id, occurred_at, reason "
                "FROM policy_events ORDER BY sequence"
            ).fetchall()
        return [dict(row) for row in rows]
