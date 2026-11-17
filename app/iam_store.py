"""SQLite-backed authoritative IAM snapshot and append-only mutation audit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.iam import Membership, Principal


class SQLiteIAMStore:
    """Persist current principal memberships and an append-only audit trail.

    Role/group/document graph persistence is deliberately not implied here; this
    first repository slice makes current membership durable and refreshable.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS iam_principals (
                    user_id TEXT PRIMARY KEY,
                    memberships_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS iam_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def put_principal(
        self, principal: Principal, *, occurred_at: str = "local"
    ) -> None:
        """Atomically replace membership snapshot and record the mutation."""
        if not occurred_at.strip():
            raise ValueError("occurred_at must be non-empty")
        memberships = [item.model_dump(mode="json") for item in principal.memberships]
        serialized = json.dumps(memberships, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO iam_principals(user_id, memberships_json) VALUES (?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET memberships_json=excluded.memberships_json",
                    (principal.user_id, serialized),
                )
                connection.execute(
                    "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                    "VALUES (?, ?, ?, ?)",
                    ("principal.updated", principal.user_id, occurred_at, serialized),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def delete_principal(self, user_id: str, *, occurred_at: str = "local") -> bool:
        """Remove a principal to revoke its memberships and audit the change.

        Returns False for an absent principal; no redundant event is recorded.
        The deletion and its audit event share one transaction.
        """
        if not occurred_at.strip():
            raise ValueError("occurred_at must be non-empty")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "DELETE FROM iam_principals WHERE user_id=?", (user_id,)
                )
                if cursor.rowcount == 0:
                    connection.commit()
                    return False
                connection.execute(
                    "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                    "VALUES (?, ?, ?, ?)",
                    ("principal.deleted", user_id, occurred_at, "{}"),
                )
                connection.commit()
                return True
            except BaseException:
                connection.rollback()
                raise

    def get_principal(self, user_id: str) -> Principal | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, memberships_json FROM iam_principals WHERE user_id=?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        memberships = tuple(
            Membership.model_validate(item)
            for item in json.loads(row["memberships_json"])
        )
        return Principal(user_id=row["user_id"], memberships=memberships)

    def list_principals(self) -> tuple[Principal, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT user_id, memberships_json FROM iam_principals ORDER BY user_id"
            ).fetchall()
        return tuple(
            Principal(
                user_id=row["user_id"],
                memberships=tuple(
                    Membership.model_validate(item)
                    for item in json.loads(row["memberships_json"])
                ),
            )
            for row in rows
        )

    def audit_events(self) -> list[dict[str, str]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT action, subject_id, occurred_at, details_json FROM iam_audit_events ORDER BY sequence"
            ).fetchall()
        return [dict(row) for row in rows]
