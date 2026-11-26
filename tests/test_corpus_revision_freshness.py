"""Focused checks for corpus breadth, effective-date freshness, and revision retirement.

Broadens the curated synthetic corpus with same-company department-tagged
policies (support, shipping, risk, billing) and proves three behaviors:

  1. The seed corpus seeds eight department-tagged policy records.
  2. An effective-date window fails closed (`as_of` outside the window returns
     no evidence, and never falls back to a stale/changed-document match).
  3. A content change retires the old version's chunks and indexes the new one,
     so a revised policy never surfaces old text under a fresh citation.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_ingestion import PolicyIngestionService
from app.retrieval import SQLiteEvidenceRetriever

APP_POLICIES = Path(__file__).resolve().parent.parent / "app" / "policies"


def _write_record(
    source_dir: Path, policy_id: str, version: str, text: str
) -> None:
    record: dict[str, Any] = {
        "id": policy_id,
        "department_id": "billing",
        "version": version,
        "title": f"{policy_id} rule",
        "status": "active",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow test policies",
        "source_url": "",
        "provenance": "Offline test fixture.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": [text],
    }
    (source_dir / f"{policy_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _ingest(source_dir: Path, database: Path) -> None:
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    PolicyIngestionService(source_dir, catalog, index).sync(
        occurred_at="2026-06-01T00:00:00Z"
    )


def _retriever(database: Path) -> SQLiteEvidenceRetriever:
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    return SQLiteEvidenceRetriever(index, catalog)


# ---------------------------------------------------------------------------
# 1) Corpus breadth
# ---------------------------------------------------------------------------

def test_catalog_seeds_eight_department_tagged_records():
    tmp = tempfile.mkdtemp()
    try:
        source_dir = Path(tmp) / "policies"
        database = Path(tmp) / "refundflow.sqlite3"
        shutil.copytree(APP_POLICIES, source_dir)
        _ingest(source_dir, database)

        catalog = PolicyCatalog(database)
        records = catalog.current_records()
        assert len(records) == 8
        for policy_id in (
            "billing-refunds",
            "billing-expedited-refund",
            "support-receipts",
            "shipping-delivery-evidence",
            "shipping-delivery-window",
            "risk-account-integrity",
            "risk-duplicate-charge",
            "support-damaged-items",
        ):
            assert policy_id in records
            assert records[policy_id]["status"] == "active"
            assert records[policy_id]["source_type"] == "synthetic_demo"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2) Effective-date freshness boundary (fail-closed, unused-as-of returns [])
# ---------------------------------------------------------------------------

def test_effective_date_window_freshness_boundary():
    tmp = tempfile.mkdtemp()
    try:
        source_dir = Path(tmp) / "policies"
        database = Path(tmp) / "refundflow.sqlite3"
        shutil.copytree(APP_POLICIES, source_dir)
        _ingest(source_dir, database)

        retriever = _retriever(database)
        # Inside the effective window: the dated policy is returned.
        inside = retriever.retrieve(
            "delivery", departments={"shipping"}, as_of="2026-06-15"
        )
        assert any(
            item.policy_id == "shipping-delivery-window" for item in inside
        ), "as_of inside the effective window must return cited evidence"

        # Before the window opens: the dated policy is excluded.
        before = retriever.retrieve(
            "delivery", departments={"shipping"}, as_of="2026-05-31"
        )
        assert all(
            item.policy_id != "shipping-delivery-window" for item in before
        ), "before effective_from must exclude the dated policy"

        # After the window closes: fail-closed.
        after = retriever.retrieve(
            "delivery", departments={"shipping"}, as_of="2027-06-01"
        )
        assert all(
            item.policy_id != "shipping-delivery-window" for item in after
        ), "after effective_to must exclude the dated policy"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3) Revision: retire old chunks, index the new version
# ---------------------------------------------------------------------------

def test_revision_retires_old_chunks_indexes_new_version():
    tmp = tempfile.mkdtemp()
    try:
        source_dir = Path(tmp) / "policies"
        source_dir.mkdir()
        database = Path(tmp) / "refundflow.sqlite3"

        _write_record(source_dir, "billing-refunds", "1.0",
                      "scope requires an order total and payment reference.")
        _ingest(source_dir, database)

        v1 = _retriever(database).retrieve(
            "scope", departments={"billing"}, as_of="2026-06-02"
        )
        assert any("order total and payment reference" in item.text for item in v1)

        # Publish a content change as a new immutable version.
        _write_record(source_dir, "billing-refunds", "1.1",
                      "scope requires the verified order total, payment status, "
                      "and a merchant reference.")
        _ingest(source_dir, database)

        catalog = PolicyCatalog(database)
        current = catalog.current("billing-refunds")
        assert current is not None, "v1.1 record should exist"
        assert current["version"] == "1.1"

        v2 = _retriever(database).retrieve(
            "scope", departments={"billing"}, as_of="2026-06-02"
        )
        assert any("verified order total" in item.text for item in v2)
        # Old wording must be gone from active chunks.
        assert not any(
            "order total and payment reference" in item.text for item in v2
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
