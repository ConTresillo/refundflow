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
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS iam_principals (
                    user_id TEXT PRIMARY KEY,
                    memberships_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS iam_groups (
                    tenant_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, group_id)
                );
                CREATE TABLE IF NOT EXISTS iam_roles (
                    tenant_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, role_id)
                );
                CREATE TABLE IF NOT EXISTS iam_permissions (
                    tenant_id TEXT NOT NULL,
                    permission_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, permission_id)
                );
                CREATE TABLE IF NOT EXISTS iam_role_permissions (
                    tenant_id TEXT NOT NULL,
                    role_id TEXT NOT NULL,
                    permission_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, role_id, permission_id),
                    FOREIGN KEY (tenant_id, role_id)
                        REFERENCES iam_roles(tenant_id, role_id),
                    FOREIGN KEY (tenant_id, permission_id)
                        REFERENCES iam_permissions(tenant_id, permission_id)
                );
                CREATE TABLE IF NOT EXISTS iam_audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS iam_authorization_revision (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    revision INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO iam_authorization_revision(singleton, revision)
                    VALUES (1, 0);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def _advance_authorization_revision(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE iam_authorization_revision SET revision=revision+1 WHERE singleton=1"
        )

    def put_principal(
        self, principal: Principal, *, occurred_at: str = "local"
    ) -> None:
        """Atomically replace membership snapshot, advance auth revision, and audit."""
        if not occurred_at.strip():
            raise ValueError("occurred_at must be non-empty")
        memberships = [item.model_dump(mode="json") for item in principal.memberships]
        serialized = json.dumps(memberships, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for membership in principal.memberships:
                    tenant_exists = connection.execute(
                        "SELECT 1 FROM iam_roles WHERE tenant_id=? UNION SELECT 1 FROM iam_groups WHERE tenant_id=? LIMIT 1",
                        (membership.tenant_id, membership.tenant_id),
                    ).fetchone()
                    if tenant_exists is None and not (membership.role_ids or membership.group_ids):
                        raise ValueError("tenant has no roles or groups")
                    for role_id in membership.role_ids:
                        exists = connection.execute(
                            "SELECT 1 FROM iam_roles WHERE tenant_id=? AND role_id=?",
                            (membership.tenant_id, role_id),
                        ).fetchone()
                        if exists is None:
                            raise ValueError("role does not belong to tenant")
                    for group_id in membership.group_ids:
                        exists = connection.execute(
                            "SELECT 1 FROM iam_groups WHERE tenant_id=? AND group_id=?",
                            (membership.tenant_id, group_id),
                        ).fetchone()
                        if exists is None:
                            raise ValueError("group does not belong to tenant")
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
                connection.execute(
                    "UPDATE iam_authorization_revision SET revision=revision+1 WHERE singleton=1"
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def put_group(
        self, tenant_id: str, group_id: str, *, occurred_at: str = "local"
    ) -> None:
        """Create an auditable tenant-scoped group, idempotently."""
        if not tenant_id.strip() or not group_id.strip() or not occurred_at.strip():
            raise ValueError("tenant_id, group_id, and occurred_at must be non-empty")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO iam_groups(tenant_id, group_id) VALUES (?, ?)",
                    (tenant_id, group_id),
                )
                if cursor.rowcount:
                    connection.execute(
                        "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            "group.created",
                            group_id,
                            occurred_at,
                            json.dumps({"tenant_id": tenant_id}, sort_keys=True),
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def list_groups(self, tenant_id: str) -> tuple[str, ...]:
        """Return only group IDs owned by the requested tenant."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT group_id FROM iam_groups WHERE tenant_id=? ORDER BY group_id",
                (tenant_id,),
            ).fetchall()
        return tuple(row["group_id"] for row in rows)

    def put_role(
        self, tenant_id: str, role_id: str, *, occurred_at: str = "local"
    ) -> None:
        """Create a tenant-scoped role and atomically append its audit event."""
        if not tenant_id.strip() or not role_id.strip() or not occurred_at.strip():
            raise ValueError("tenant_id, role_id, and occurred_at must be non-empty")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO iam_roles(tenant_id, role_id) VALUES (?, ?)",
                    (tenant_id, role_id),
                )
                if cursor.rowcount:
                    connection.execute(
                        "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            "role.created",
                            role_id,
                            occurred_at,
                            json.dumps({"tenant_id": tenant_id}, sort_keys=True),
                        ),
                    )
                    self._advance_authorization_revision(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def list_roles(self, tenant_id: str) -> tuple[str, ...]:
        """Return role IDs for this tenant only, in stable order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT role_id FROM iam_roles WHERE tenant_id=? ORDER BY role_id",
                (tenant_id,),
            ).fetchall()
        return tuple(row["role_id"] for row in rows)

    def put_permission(
        self, tenant_id: str, permission_id: str, *, occurred_at: str = "local"
    ) -> None:
        """Create a tenant-scoped permission and atomically audit insertion."""
        if not tenant_id.strip() or not permission_id.strip() or not occurred_at.strip():
            raise ValueError("tenant_id, permission_id, and occurred_at must be non-empty")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO iam_permissions(tenant_id, permission_id) VALUES (?, ?)",
                    (tenant_id, permission_id),
                )
                if cursor.rowcount:
                    connection.execute(
                        "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            "permission.created",
                            permission_id,
                            occurred_at,
                            json.dumps({"tenant_id": tenant_id}, sort_keys=True),
                        ),
                    )
                    self._advance_authorization_revision(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def list_permissions(self, tenant_id: str) -> tuple[str, ...]:
        """Return permission IDs owned by this tenant in stable order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT permission_id FROM iam_permissions WHERE tenant_id=? ORDER BY permission_id",
                (tenant_id,),
            ).fetchall()
        return tuple(row["permission_id"] for row in rows)

    def assign_role_permission(
        self,
        tenant_id: str,
        role_id: str,
        permission_id: str,
        *,
        occurred_at: str = "local",
    ) -> None:
        """Explicitly grant a tenant-owned permission to a tenant-owned role."""
        if not all(value.strip() for value in (tenant_id, role_id, permission_id, occurred_at)):
            raise ValueError("tenant, role, permission, and occurred_at must be non-empty")
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                exists = connection.execute(
                    "SELECT 1 FROM iam_roles WHERE tenant_id=? AND role_id=?",
                    (tenant_id, role_id),
                ).fetchone()
                if exists is None:
                    raise ValueError("role does not belong to tenant")
                exists = connection.execute(
                    "SELECT 1 FROM iam_permissions WHERE tenant_id=? AND permission_id=?",
                    (tenant_id, permission_id),
                ).fetchone()
                if exists is None:
                    raise ValueError("permission does not belong to tenant")
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO iam_role_permissions VALUES (?, ?, ?)",
                    (tenant_id, role_id, permission_id),
                )
                if cursor.rowcount:
                    connection.execute(
                        "INSERT INTO iam_audit_events(action, subject_id, occurred_at, details_json) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            "role.permission.assigned",
                            role_id,
                            occurred_at,
                            json.dumps({"tenant_id": tenant_id, "permission_id": permission_id}, sort_keys=True),
                        ),
                    )
                    self._advance_authorization_revision(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def list_role_permissions(self, tenant_id: str, role_id: str) -> tuple[str, ...]:
        """List explicit permissions for a role, restricted to its tenant."""
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            rows = connection.execute(
                "SELECT permission_id FROM iam_role_permissions "
                "WHERE tenant_id=? AND role_id=? ORDER BY permission_id",
                (tenant_id, role_id),
            ).fetchall()
        return tuple(row["permission_id"] for row in rows)

    def authorization_snapshot(self) -> tuple[Principal, ...]:
        """Load persisted principals as a refresh input, with no implicit grants.

        Explicit role-permission links are persisted separately; this method
        still returns principals only and cannot authorize access on its own.
        """
        with closing(self._connect()) as connection:
            principals = connection.execute(
                "SELECT user_id, memberships_json FROM iam_principals ORDER BY user_id"
            ).fetchall()
        principal_models = tuple(
            Principal(
                user_id=row["user_id"],
                memberships=tuple(
                    Membership.model_validate(item)
                    for item in json.loads(row["memberships_json"])
                ),
            )
            for row in principals
        )
        return principal_models

    def authorization_principal_snapshot(self) -> tuple[int, tuple[Principal, ...]]:
        """Return principals and their revision from one consistent read snapshot."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            try:
                revision = connection.execute(
                    "SELECT revision FROM iam_authorization_revision WHERE singleton=1"
                ).fetchone()
                principals = connection.execute(
                    "SELECT user_id, memberships_json FROM iam_principals ORDER BY user_id"
                ).fetchall()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        principal_models = tuple(
            Principal(
                user_id=row["user_id"],
                memberships=tuple(
                    Membership.model_validate(item)
                    for item in json.loads(row["memberships_json"])
                ),
            )
            for row in principals
        )
        return int(revision["revision"]), principal_models

    def authorization_revision(self) -> int:
        """Return the committed authorization revision."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT revision FROM iam_authorization_revision WHERE singleton=1"
            ).fetchone()
        return int(row["revision"])

    def delete_principal(self, user_id: str, *, occurred_at: str = "local") -> bool:
        """Remove a principal, advance authorization revision and audit atomically.

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
                self._advance_authorization_revision(connection)
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
