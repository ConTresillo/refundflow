"""Incremental, local lexical policy index backed by SQLite FTS5."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class ChunkInput(TypedDict):
    """A source chunk; multiple chunks may share one section ID."""

    policy_id: str
    version: str
    department_id: str
    section_id: str
    text: str


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    policy_id: str
    version: str
    department_id: str
    section_id: str
    text: str


@dataclass(frozen=True)
class IngestResult:
    processed: int
    reused: int
    retired: int
    chunks: tuple[IndexedChunk, ...]


class SQLitePolicyIndex:
    """Keep stable content-addressed chunks and an atomically updated FTS index."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with closing(self._connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS policy_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    department_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    body TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(policy_id, version, section_id, text_hash)
                );
                CREATE INDEX IF NOT EXISTS ix_policy_chunks_scope
                    ON policy_chunks(department_id, policy_id, version, active);
                CREATE VIRTUAL TABLE IF NOT EXISTS policy_chunks_fts
                    USING fts5(chunk_id UNINDEXED, body);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _prepare(chunks: Iterable[ChunkInput]) -> list[IndexedChunk]:
        prepared = []
        for item in chunks:
            body = " ".join(item["text"].split())
            identity = (item["policy_id"], item["version"], item["section_id"])
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            material = "\0".join((*identity, digest, "chunk-v1"))
            prepared.append(
                IndexedChunk(
                    hashlib.sha256(material.encode("utf-8")).hexdigest(),
                    item["policy_id"],
                    item["version"],
                    item["department_id"],
                    item["section_id"],
                    body,
                )
            )
        return prepared

    def _reconcile(
        self,
        db: sqlite3.Connection,
        prepared: list[IndexedChunk],
        complete_versions: Iterable[tuple[str, str]],
    ) -> IngestResult:
        scope_departments: dict[tuple[str, str], str] = {}
        for chunk in prepared:
            scope = (chunk.policy_id, chunk.version)
            department = scope_departments.setdefault(scope, chunk.department_id)
            if department != chunk.department_id:
                raise ValueError("all chunks in one policy version must use one department")

        policy_scope = set(complete_versions)
        policy_scope.update(scope_departments)
        desired_ids = {chunk.chunk_id for chunk in prepared}
        processed = reused = retired = 0
        for (policy_id, version), department_id in scope_departments.items():
            existing_departments = {
                row["department_id"]
                for row in db.execute(
                    "SELECT DISTINCT department_id FROM policy_chunks WHERE policy_id=? AND version=?",
                    (policy_id, version),
                ).fetchall()
            }
            if existing_departments and existing_departments != {department_id}:
                raise ValueError(
                    f"department_id is immutable for policy version {policy_id}@{version}"
                )

        for chunk in prepared:
            row = db.execute(
                "SELECT body, department_id, active FROM policy_chunks WHERE chunk_id=?",
                (chunk.chunk_id,),
            ).fetchone()
            if row and row["active"] and row["body"] == chunk.text and row["department_id"] == chunk.department_id:
                reused += 1
                continue
            db.execute(
                """INSERT INTO policy_chunks
                (chunk_id, policy_id, version, department_id, section_id, text_hash, body, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    department_id=excluded.department_id, body=excluded.body, active=1""",
                (
                    chunk.chunk_id,
                    chunk.policy_id,
                    chunk.version,
                    chunk.department_id,
                    chunk.section_id,
                    hashlib.sha256(chunk.text.encode()).hexdigest(),
                    chunk.text,
                ),
            )
            db.execute("DELETE FROM policy_chunks_fts WHERE chunk_id=?", (chunk.chunk_id,))
            db.execute(
                "INSERT INTO policy_chunks_fts(chunk_id, body) VALUES (?, ?)",
                (chunk.chunk_id, chunk.text),
            )
            processed += 1

        if policy_scope:
            for row in db.execute(
                "SELECT chunk_id, policy_id, version FROM policy_chunks WHERE active=1"
            ).fetchall():
                scope = (row["policy_id"], row["version"])
                if scope in policy_scope and row["chunk_id"] not in desired_ids:
                    db.execute("UPDATE policy_chunks SET active=0 WHERE chunk_id=?", (row["chunk_id"],))
                    db.execute("DELETE FROM policy_chunks_fts WHERE chunk_id=?", (row["chunk_id"],))
                    retired += 1
        return IngestResult(processed, reused, retired, tuple(prepared))

    def ingest(
        self,
        chunks: Iterable[ChunkInput],
        *,
        complete_versions: Iterable[tuple[str, str]] = (),
        before_commit: Callable[[sqlite3.Connection], None] | None = None,
    ) -> IngestResult:
        """Atomically replace complete version snapshots in an index transaction."""
        prepared = self._prepare(chunks)
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                result = self._reconcile(db, prepared, complete_versions)
                if before_commit:
                    before_commit(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return result

    def ingest_in_transaction(
        self,
        connection: sqlite3.Connection,
        chunks: Iterable[ChunkInput],
        *,
        complete_versions: Iterable[tuple[str, str]] = (),
    ) -> IngestResult:
        """Reconcile a snapshot inside a caller-owned SQLite transaction."""
        if not connection.in_transaction:
            raise ValueError("ingest_in_transaction requires an active transaction")
        prepared = self._prepare(chunks)
        return self._reconcile(connection, prepared, complete_versions)

    def search(
        self,
        query: str,
        *,
        department_id: str,
        policy_id: str | None = None,
        version: str | None = None,
        top_k: int = 5,
    ) -> list[IndexedChunk]:
        """Search with FTS5 OR semantics, optionally scoped to policy/version."""
        terms = [
            word.strip('.,;:!?()[]{}\"\'')
            for word in query.split()
        ]
        terms = list(dict.fromkeys(term for term in terms if term))
        if not terms or top_k <= 0:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        params = (match, department_id, policy_id, policy_id, version, version, top_k)
        with closing(self._connect()) as db:
            try:
                rows = db.execute(
                    """SELECT c.chunk_id, c.policy_id, c.version, c.department_id,
                        c.section_id, c.body
                    FROM policy_chunks_fts f JOIN policy_chunks c ON c.chunk_id=f.chunk_id
                    WHERE policy_chunks_fts MATCH ? AND c.department_id=? AND c.active=1
                        AND (? IS NULL OR c.policy_id=?)
                        AND (? IS NULL OR c.version=?)
                    ORDER BY rank, c.chunk_id LIMIT ?""",
                    params,
                ).fetchall()
            except sqlite3.OperationalError as error:
                if str(error).startswith("fts5: syntax error"):
                    return []
                raise
        return [
            IndexedChunk(
                row["chunk_id"],
                row["policy_id"],
                row["version"],
                row["department_id"],
                row["section_id"],
                row["body"],
            )
            for row in rows
        ]
