import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeError, PolicyChangeService
from app.policy_ingestion import PolicyIngestionService


def write_policy(
    directory: Path, policy_id: str, version: str, excerpts: list[str]
) -> None:
    record = {
        "id": policy_id,
        "department_id": "billing",
        "version": version,
        "title": f"{policy_id} rules",
        "status": "active",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow test policies",
        "source_url": "",
        "provenance": "Authored for an offline test.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": excerpts,
    }
    (directory / f"{policy_id}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def make_service(tmp_path: Path):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    write_policy(
        source_dir, "billing-refunds", "1.0", ["legacyclaim requires an order receipt."]
    )
    write_policy(
        source_dir, "billing-fees", "1.0", ["Fee disputes require a billing review."]
    )
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    ingestion = PolicyIngestionService(source_dir, catalog, index)
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    service = PolicyChangeService(
        source_dir,
        catalog,
        index,
        clock=lambda: datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    return source_dir, database, catalog, index, ingestion, service


def make_draft(service: PolicyChangeService) -> dict:
    policy = next(
        item
        for item in service.list_policies()
        if item["policy_id"] == "billing-refunds"
    )
    return service.create_draft(
        policy_id="billing-refunds",
        expected_version=policy["version"],
        expected_hash=policy["content_hash"],
        new_version="2.0",
        title="Billing Refund Eligibility — revised",
        effective_from="2026-06-02",
        effective_to=None,
        excerpts=["Refund claims require an order receipt and a payment reference."],
    )


def test_draft_shows_exact_content_diff_without_changing_active_rag(tmp_path):
    _, _, catalog, index, _, service = make_service(tmp_path)

    draft = make_draft(service)

    assert draft["state"] == "draft"
    assert draft["base_version"] == "1.0"
    assert draft["new_version"] == "2.0"
    assert "-legacyclaim requires an order receipt." in draft["diff"]
    assert (
        "+Refund claims require an order receipt and a payment reference."
        in draft["diff"]
    )
    assert "title" in draft["changed_fields"]
    assert "excerpts" in draft["changed_fields"]
    assert catalog.current("billing-refunds")["version"] == "1.0"
    assert [
        item.text
        for item in index.search(
            "receipt", department_id="billing", policy_id="billing-refunds"
        )
    ] == ["legacyclaim requires an order receipt."]
    assert (
        index.search(
            "payment reference", department_id="billing", policy_id="billing-refunds"
        )
        == []
    )


def test_publication_requires_exact_diff_review_and_is_idempotent(tmp_path):
    _, _, catalog, index, _, service = make_service(tmp_path)
    draft = make_draft(service)

    with pytest.raises(PolicyChangeError, match="review"):
        service.publish_draft(draft["draft_id"])
    with pytest.raises(PolicyChangeError, match="diff"):
        service.review_draft(draft["draft_id"], diff_hash="not-the-diff")

    reviewed = service.review_draft(draft["draft_id"], diff_hash=draft["diff_hash"])
    assert reviewed["state"] == "reviewed"
    report = service.publish_draft(draft["draft_id"])
    replay = service.publish_draft(draft["draft_id"])

    assert replay["run_id"] == report["run_id"]
    assert report["status"] == "succeeded"
    assert report["index_reflected"] is True
    assert report["counts"] == {
        "added": 0,
        "changed": 1,
        "removed": 0,
        "unchanged": 1,
        "chunks_processed": 1,
        "chunks_reused": 0,
        "chunks_retired": 1,
    }
    assert catalog.current("billing-refunds")["version"] == "2.0"
    assert catalog.version("billing-refunds", "1.0")["status"] == "superseded"
    assert [
        item.text
        for item in index.search(
            "legacyclaim", department_id="billing", policy_id="billing-refunds"
        )
    ] == []
    evidence = index.search(
        "payment reference", department_id="billing", policy_id="billing-refunds"
    )
    assert len(evidence) == 1
    assert evidence[0].version == "2.0"
    assert service.list_policies()[0]["index_reflected"] is True
    assert service.history()[0]["run_id"] == report["run_id"]


def test_stale_draft_is_rejected_after_source_and_catalog_move(tmp_path):
    source_dir, _, catalog, index, ingestion, service = make_service(tmp_path)
    draft = make_draft(service)
    write_policy(
        source_dir,
        "billing-refunds",
        "1.5",
        ["Refund claims require manager approval."],
    )
    ingestion.sync(occurred_at="2026-06-02T00:00:00Z")
    service.review_draft(draft["draft_id"], diff_hash=draft["diff_hash"])

    with pytest.raises(PolicyChangeError, match="stale"):
        service.publish_draft(draft["draft_id"])

    assert catalog.current("billing-refunds")["version"] == "1.5"
    assert [
        item.text
        for item in index.search(
            "manager approval", department_id="billing", policy_id="billing-refunds"
        )
    ] == ["Refund claims require manager approval."]


def test_failed_publication_restores_source_and_keeps_prior_rag_snapshot(tmp_path):
    source_dir, database, catalog, index, _, service = make_service(tmp_path)
    draft = make_draft(service)
    service.review_draft(draft["draft_id"], diff_hash=draft["diff_hash"])
    original_source = (source_dir / "billing-refunds.json").read_bytes()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TRIGGER fail_policy_publish BEFORE INSERT ON policy_events
            WHEN NEW.event_type = 'published'
            BEGIN SELECT RAISE(ABORT, 'injected publish failure'); END"""
        )

    with pytest.raises(
        PolicyChangeError, match="prior source and active RAG snapshot were restored"
    ):
        service.publish_draft(draft["draft_id"])

    assert (source_dir / "billing-refunds.json").read_bytes() == original_source
    assert catalog.current("billing-refunds")["version"] == "1.0"
    assert [
        item.text
        for item in index.search(
            "receipt", department_id="billing", policy_id="billing-refunds"
        )
    ] == ["legacyclaim requires an order receipt."]
    assert (
        index.search(
            "payment reference", department_id="billing", policy_id="billing-refunds"
        )
        == []
    )
    assert service.history()[0]["status"] == "failed"
    assert service.history()[0]["source_restored"] is True
