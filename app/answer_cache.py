"""Bounded exact answer cache: replaceable port plus SQLite adapter.

This is the "exact" answer-cache layer, independent of the TTL snapshot cache in
``retrieval.py``.  A cached answer is a fully-resolved, authority-filtered
evidence envelope keyed on the *whole* valid observation envelope:

  - normalized query
  - requested department scope
  - as-of date (exact eligibility)
  - allowed document grants (permission scope)
  - evidence mode (freshness / ttl / critical)
  - answer-cache mode ("exact")
  - caller identity (tenant-qualified subject + tenant)
  - catalog snapshot hash (content/version provenance)
  - effective date window (start/end)

An envelope is only served when every one of those fields still matches.  A
change to the policy catalog, the effective window, the grant set, or the
caller's identity immediately invalidates the envelope, so stale or
unauthorized answers can never be served from the cache.

Storage is bounded (fixed-size LRU table plus a global byte cap) so the cache
can never grow without limit.  A corrupt on-disk row is treated as a miss and
rebuilt; the failure is local and never degrades authorization.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

__all__ = [
    "AnswerCache",
    "AnswerCacheError",
    "SqliteBoundedAnswerCache",
]


class AnswerCacheError(Exception):
    """Base class for answer-cache failures."""


class AnswerCache:
    """Port for bounded exact answer caches."""

    def get(self, key: CacheKey) -> tuple[list[dict[str, Any]], str] | None:
        """Return ``(envelope, snapshot_hash)`` or ``None`` (miss or corrupt)."""

    def put(
        self,
        key: CacheKey,
        envelope: list[dict[str, Any]],
        snapshot_hash: str,
        auth_hash: str,
        ttl_seconds: float,
        size_bytes: int,
    ) -> None:
        """Store an envelope under *key*."""

    def invalidate_snapshot(self, snapshot_hash: str) -> int:
        """Invalidate every envelope bound to *snapshot_hash*; return count."""

    def invalidate_auth(self, auth_hash: str) -> int:
        """Invalidate every envelope bound to *auth_hash*; return count."""

    def close(self) -> None:
        raise NotImplementedError


class CacheKey:
    """Immutable, hashable record of one retrieval envelope's valid inputs."""

    __slots__ = (
        "allowed_policy_ids",
        "answer_cache",
        "as_of",
        "auth_subject",
        "auth_tenant",
        "departments",
        "effective_from",
        "effective_to",
        "evidence_mode",
        "query",
        "snapshot_hash",
    )

    def __init__(
        self,
        *,
        query: str,
        departments: Iterable[str],
        as_of: str,
        allowed_policy_ids: Iterable[str] | None,
        evidence_mode: str,
        answer_cache: str,
        auth_subject: str,
        auth_tenant: str,
        snapshot_hash: str,
        effective_from: str | None = None,
        effective_to: str | None = None,
    ) -> None:
        self.query = query
        self.departments = tuple(sorted(departments))
        self.as_of = as_of
        self.allowed_policy_ids = tuple(sorted(allowed_policy_ids or ()))
        self.evidence_mode = evidence_mode
        self.answer_cache = answer_cache
        self.auth_subject = auth_subject
        self.auth_tenant = auth_tenant
        self.snapshot_hash = snapshot_hash
        self.effective_from = effective_from
        self.effective_to = effective_to

    def __hash__(self) -> int:
        return hash(
            (
                self.query,
                self.departments,
                self.as_of,
                self.allowed_policy_ids,
                self.evidence_mode,
                self.answer_cache,
                self.auth_subject,
                self.auth_tenant,
                self.snapshot_hash,
                self.effective_from,
                self.effective_to,
            )
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CacheKey):
            return NotImplemented
        return tuple(self) == tuple(other)

    def __iter__(self):
        return iter(
            (
                self.query,
                self.departments,
                self.as_of,
                self.allowed_policy_ids,
                self.evidence_mode,
                self.answer_cache,
                self.auth_subject,
                self.auth_tenant,
                self.snapshot_hash,
                self.effective_from,
                self.effective_to,
            )
        )


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def _envelope_size(envelope: Iterable[dict[str, Any]]) -> int:
    return sum(len(json.dumps(item, sort_keys=True).encode("utf-8")) for item in envelope)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key_hash(key: CacheKey) -> str:
    """Deterministic storage key for one retrieval envelope's valid inputs."""

    return _sha256("\0".join(str(part) for part in key))


class SqliteBoundedAnswerCache(AnswerCache):
    """SQLite-backed bounded exact answer cache (LRU rows + global byte cap)."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS answer_cache (
        key_hash TEXT PRIMARY KEY,
        envelope TEXT NOT NULL,
        snapshot_hash TEXT NOT NULL,
        auth_hash TEXT NOT NULL,
        expires_at REAL NOT NULL,
        size_bytes INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_answer_cache_expires ON answer_cache(expires_at);
    CREATE INDEX IF NOT EXISTS ix_answer_cache_snapshot ON answer_cache(snapshot_hash);
    CREATE INDEX IF NOT EXISTS ix_answer_cache_auth ON answer_cache(auth_hash);
    """

    def __init__(
        self,
        database: str | Path,
        *,
        max_entries: int = 256,
        max_bytes: int = 64 * 1024 * 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._clock = clock
        self._conn_handle: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        with closing(sqlite3.connect(self.database, timeout=5)) as connection:
            connection.executescript(self._SCHEMA)
            connection.commit()

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _touch(self, key_hash: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE answer_cache SET updated_at=? WHERE key_hash=?",
                (self._clock(), key_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def get(self, key: CacheKey) -> tuple[list[dict[str, Any]], str] | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT envelope, snapshot_hash, auth_hash, expires_at, size_bytes, updated_at FROM answer_cache WHERE key_hash=?",
                (key_hash(key),),
            ).fetchone()
            if row is None:
                return None
            # Corrupt or expired row is silently evicted (never served).
            try:
                if self._clock() > row["expires_at"]:
                    conn.execute("DELETE FROM answer_cache WHERE key_hash=?", (key_hash(key),))
                    conn.commit()
                    return None
                envelope = json.loads(row["envelope"])
                auth_hash = row["auth_hash"]
            except (sqlite3.Error, ValueError, TypeError):
                conn.execute("DELETE FROM answer_cache WHERE key_hash=?", (key_hash(key),))
                conn.commit()
                return None
        finally:
            conn.close()
        self._touch(key_hash(key))
        return envelope, auth_hash

    def put(
        self,
        key: CacheKey,
        envelope: list[dict[str, Any]],
        snapshot_hash: str,
        auth_hash: str,
        ttl_seconds: float,
        size_bytes: int,
    ) -> None:
        # Fail-closed: never cache an envelope that exceeds the byte cap.
        if self.max_bytes > 0 and size_bytes > self.max_bytes:
            return
        conn = self._conn()
        try:
            now = self._clock()
            payload = {
                "key_hash": key_hash(key),
                "envelope": json.dumps(envelope, sort_keys=True),
                "snapshot_hash": snapshot_hash,
                "auth_hash": auth_hash,
                "expires_at": now + ttl_seconds,
                "size_bytes": size_bytes,
                "created_at": now,
                "updated_at": now,
            }
            conn.execute(
                """
                INSERT INTO answer_cache
                    (key_hash, envelope, snapshot_hash, auth_hash, expires_at,
                     size_bytes, created_at, updated_at)
                VALUES (:key_hash, :envelope, :snapshot_hash, :auth_hash,
                        :expires_at, :size_bytes, :created_at, :updated_at)
                ON CONFLICT(key_hash) DO UPDATE SET
                    envelope=excluded.envelope,
                    snapshot_hash=excluded.snapshot_hash,
                    auth_hash=excluded.auth_hash,
                    expires_at=excluded.expires_at,
                    size_bytes=excluded.size_bytes,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
            conn.commit()
        finally:
            conn.close()
        self._evict()

    def invalidate_snapshot(self, snapshot_hash: str) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM answer_cache WHERE snapshot_hash=?", (snapshot_hash,)
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()

    def invalidate_auth(self, auth_hash: str) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM answer_cache WHERE auth_hash=?", (auth_hash,)
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()

    def close(self) -> None:
        return

    def _evict(self) -> None:
        if self.max_entries <= 0:
            return
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS n FROM answer_cache").fetchone()
            while int(cur["n"]) > self.max_entries:
                oldest = conn.execute(
                    "SELECT key_hash FROM answer_cache ORDER BY expires_at ASC, rowid ASC LIMIT 1"
                ).fetchone()
                if oldest is None:
                    break
                conn.execute("DELETE FROM answer_cache WHERE key_hash=?", (oldest["key_hash"],))
                cur = conn.execute("SELECT COUNT(*) AS n FROM answer_cache").fetchone()
            conn.commit()
        finally:
            conn.close()
    def _evict(self) -> None:
        if self.max_entries <= 0:
            return
        conn = self._conn()
        try:
            cur = conn.execute("SELECT COUNT(*) AS n FROM answer_cache").fetchone()
            while int(cur["n"]) > self.max_entries:
                oldest = conn.execute(
                    "SELECT key_hash FROM answer_cache ORDER BY expires_at ASC, rowid ASC LIMIT 1"
                ).fetchone()
                if oldest is None:
                    break
                conn.execute("DELETE FROM answer_cache WHERE key_hash=?", (oldest["key_hash"],))
                cur = conn.execute("SELECT COUNT(*) AS n FROM answer_cache").fetchone()
            conn.commit()
        finally:
            conn.close()
