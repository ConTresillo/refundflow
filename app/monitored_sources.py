"""Revision tracking for the monitored external source.

The monitored source ``refundflow-opportunity`` is maintained outside this
repository and its output is a revision hash. This thin adapter persists the
observed revision hash (plus a pointer timestamp and status) so that a change in
the source output is visible and re-checkable locally, without fetching content
and without touching the policy watermark table.

It exists purely to make the monitored-source change durable and testable; it
never fetches, never mutates the policy corpus, and never performs external
actions.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS monitored_sources (
    source_id TEXT NOT NULL PRIMARY KEY,
    observed_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('current', 'failed'))
)
"""

INSERT_SQL = """
INSERT INTO monitored_sources (source_id, observed_hash, observed_at, status)
VALUES (?, ?, ?, 'current')
ON CONFLICT(source_id) DO UPDATE SET
    observed_hash=excluded.observed_hash,
    observed_at=excluded.observed_at,
    status='current'
"""

SELECT_SQL = """
SELECT source_id, observed_hash, observed_at, status
FROM monitored_sources WHERE source_id = ?
"""

DEFAULT_MONITORED_SOURCE = "refundflow-opportunity"


class SQLiteMonitoredSources:
    """Persist and read the observed revision hash of the monitored source."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(CREATE_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _coerce_source_id(source_id: Any) -> str:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        return source_id.strip()

    @staticmethod
    def _coerce_hash(observed_hash: Any) -> str:
        if not isinstance(observed_hash, str) or not observed_hash.strip():
            raise ValueError("observed_hash must be a non-empty string")
        return observed_hash.strip()

    def record(
        self,
        source_id: str,
        observed_hash: str,
        observed_at: str | None = None,
    ) -> None:
        """Record an observed revision for the monitored source."""
        source_id = self._coerce_source_id(source_id)
        observed_hash = self._coerce_hash(observed_hash)
        if observed_at is None or not isinstance(observed_at, str) or not observed_at.strip():
            observed_at = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            try:
                connection.execute(INSERT_SQL, (source_id, observed_hash, observed_at))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def status(self, source_id: str) -> dict[str, str] | None:
        """Return the observed revision for *source_id*, or None if unknown."""
        source_id = self._coerce_source_id(source_id)
        with closing(self._connect()) as connection:
            row = connection.execute(SELECT_SQL, (source_id,)).fetchone()
        return dict(row) if row else None
