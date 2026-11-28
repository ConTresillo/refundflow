"""Focused tests for the monitored-source revision tracker (revision freshness).

Verifies that the observed revision hash of the monitored source is recorded,
that a later revision advances the stored hash, and that invalid input fails
closed (no stale/unknown revision is accepted).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.monitored_sources import SQLiteMonitoredSources

SOURCE_ID = "refundflow-opportunity"
FIRST_OBSERVED = "1909139beb6ca77b"
# The tracked source flipped again; the freshest observed revision is pinned
# here and must be read back through the authenticated readout endpoint.
FRESH_OBSERVED = "183babc2007d8985"


def _write(db: Path, observed_hash: str, observed_at: str | None = None) -> None:
    adapter = SQLiteMonitoredSources(db)
    adapter.record(SOURCE_ID, observed_hash, observed_at)
    row = adapter.status(SOURCE_ID)
    assert row is not None, "recorded source must be readable back"
    assert row["observed_hash"] == observed_hash


def test_records_observed_revision_and_advances_hash(tmp_path):
    db = tmp_path / "refundflow.sqlite3"
    _write(db, FIRST_OBSERVED)
    _write(db, FRESH_OBSERVED)

    advanced = SQLiteMonitoredSources(db).status(SOURCE_ID)
    assert advanced is not None
    assert advanced["observed_hash"] == FRESH_OBSERVED
    assert advanced["status"] == "current"


def test_invalid_source_id_is_rejected_fails_closed(tmp_path):
    db = tmp_path / "refundflow.sqlite3"
    adapter = SQLiteMonitoredSources(db)
    with pytest.raises(ValueError):
        adapter.record("", FIRST_OBSERVED, "2026-11-26T00:00:00Z")
    assert adapter.status(SOURCE_ID) is None


def test_invalid_observed_hash_is_rejected_fails_closed(tmp_path):
    db = tmp_path / "refundflow.sqlite3"
    adapter = SQLiteMonitoredSources(db)
    with pytest.raises(ValueError):
        adapter.record(SOURCE_ID, "", "2026-11-26T00:00:00Z")
    assert adapter.status(SOURCE_ID) is None


def test_missing_source_returns_none(tmp_path):
    db = tmp_path / "refundflow.sqlite3"
    adapter = SQLiteMonitoredSources(db)
    assert adapter.status("some-other-source") is None
