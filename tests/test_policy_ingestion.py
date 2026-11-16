import json
from pathlib import Path

import pytest

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_ingestion import PolicyIngestionService


def write_policy(
    directory: Path,
    policy_id: str,
    version: str,
    excerpt: str,
    *,
    effective_from: str = "2026-01-01",
) -> None:
    record = {
        "id": policy_id,
        "department_id": "billing",
        "version": version,
        "title": f"{policy_id} policy",
        "status": "active",
        "effective_from": effective_from,
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow test policies",
        "source_url": "",
        "provenance": "Authored for an offline test.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": [excerpt],
    }
    (directory / f"{policy_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


def make_service(tmp_path: Path, *records: tuple[str, str, str]):
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    for policy_id, version, excerpt in records:
        write_policy(source_dir, policy_id, version, excerpt)
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    return source_dir, catalog, index, PolicyIngestionService(source_dir, catalog, index)



def test_status_reports_current_document_provenance_and_sync_outcome(tmp_path):
    _, catalog, _, service = make_service(
        tmp_path, ("billing-refunds", "1.0", "refundstatus clause")
    )
    assert service.status() == []
    service.sync(occurred_at="2026-06-01T00:00:00Z")

    assert service.status() == [{
        "policy_id": "billing-refunds",
        "version": "1.0",
        "department_id": "billing",
        "status": "active",
        "content_hash": catalog.current("billing-refunds")["content_hash"] if catalog.current("billing-refunds") else "",
        "provenance": "Authored for an offline test.",
        "outcome": "added",
    }]


def test_sync_is_idempotent_and_leaves_unchanged_policies_alone(tmp_path):
    _, catalog, index, service = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "refundalpha settlement rule"),
        ("billing-fees", "1.0", "feealpha evidence rule"),
    )

    first = service.sync(occurred_at="2026-06-01T00:00:00Z")
    first_events = catalog.timeline()
    first_ids = {
        item.policy_id: item.chunk_id
        for item in (
            index.search("refundalpha", department_id="billing")
            + index.search("feealpha", department_id="billing")
        )
    }
    second = service.sync(occurred_at="2026-06-02T00:00:00Z")

    assert (first.added, first.changed, first.removed, first.unchanged) == (2, 0, 0, 0)
    assert first.index.processed == 2
    assert (second.added, second.changed, second.removed, second.unchanged) == (0, 0, 0, 2)
    assert second.index.processed == second.index.retired == 0
    assert catalog.timeline() == first_events
    assert {
        item.policy_id: item.chunk_id
        for item in (
            index.search("refundalpha", department_id="billing")
            + index.search("feealpha", department_id="billing")
        )
    } == first_ids


def test_changed_version_updates_only_affected_catalog_and_index_snapshot(tmp_path):
    source_dir, catalog, index, service = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "refundlegacy clause"),
        ("billing-fees", "1.0", "feeunchanged clause"),
    )
    service.sync(occurred_at="2026-06-01T00:00:00Z")
    stable_id = index.search("feeunchanged", department_id="billing")[0].chunk_id
    write_policy(source_dir, "billing-refunds", "2.0", "refundcurrent clause")

    result = service.sync(occurred_at="2026-06-02T00:00:00Z")

    assert (result.added, result.changed, result.removed, result.unchanged) == (0, 1, 0, 1)
    assert result.index.processed == 1
    assert result.index.retired == 1
    assert catalog.current("billing-refunds")["version"] == "2.0"
    assert catalog.version("billing-refunds", "1.0")["status"] == "superseded"
    assert index.search("refundlegacy", department_id="billing") == []
    assert [item.text for item in index.search("refundcurrent", department_id="billing")] == [
        "refundcurrent clause"
    ]
    assert index.search("feeunchanged", department_id="billing")[0].chunk_id == stable_id


def test_removed_source_withdraws_policy_and_retires_its_chunks(tmp_path):
    source_dir, catalog, index, service = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "refundremoved clause"),
        ("billing-fees", "1.0", "feeavailable clause"),
    )
    service.sync(occurred_at="2026-06-01T00:00:00Z")
    (source_dir / "billing-refunds.json").unlink()

    result = service.sync(occurred_at="2026-06-02T00:00:00Z")

    assert (result.added, result.changed, result.removed, result.unchanged) == (0, 0, 1, 1)
    assert result.index.retired == 1
    assert catalog.current("billing-refunds")["status"] == "withdrawn"
    assert index.search("refundremoved", department_id="billing") == []
    assert [item.text for item in index.search("feeavailable", department_id="billing")] == [
        "feeavailable clause"
    ]


def test_future_effective_source_is_not_published_as_active(tmp_path):
    _, catalog, index, service = make_service(
        tmp_path,
        ("billing-refunds", "2.0", "futurepolicy clause"),
    )
    write_policy(
        tmp_path / "policies",
        "billing-refunds",
        "2.0",
        "futurepolicy clause",
        effective_from="2027-01-01",
    )

    with pytest.raises(ValueError, match="future effective"):
        service.sync(occurred_at="2026-06-01T00:00:00Z")

    assert catalog.current("billing-refunds") is None
    assert catalog.timeline() == []
    assert index.search("futurepolicy", department_id="billing") == []


def test_missing_source_directory_never_looks_like_total_policy_deletion(tmp_path):
    source_dir, catalog, index, service = make_service(
        tmp_path, ("billing-refunds", "1.0", "refundkept clause")
    )
    service.sync(occurred_at="2026-06-01T00:00:00Z")
    missing_service = PolicyIngestionService(tmp_path / "missing", catalog, index)

    with pytest.raises(FileNotFoundError):
        missing_service.sync(occurred_at="2026-06-02T00:00:00Z")

    assert catalog.current("billing-refunds")["status"] == "active"
    assert [item.text for item in index.search("refundkept", department_id="billing")] == [
        "refundkept clause"
    ]
    assert source_dir.is_dir()


def test_same_version_content_change_is_rejected_without_partial_mutation(tmp_path):
    source_dir, catalog, index, service = make_service(
        tmp_path, ("billing-refunds", "1.0", "refundstable clause")
    )
    service.sync(occurred_at="2026-06-01T00:00:00Z")
    events = catalog.timeline()
    write_policy(source_dir, "billing-refunds", "1.0", "refundsilentlychanged clause")

    with pytest.raises(ValueError, match="version must change"):
        service.sync(occurred_at="2026-06-02T00:00:00Z")

    assert catalog.current("billing-refunds")["version"] == "1.0"
    assert catalog.timeline() == events
    assert [item.text for item in index.search("refundstable", department_id="billing")] == [
        "refundstable clause"
    ]
    assert index.search("refundsilentlychanged", department_id="billing") == []


def test_catalog_and_index_changes_roll_back_together(tmp_path):
    source_dir, catalog, index, service = make_service(
        tmp_path, ("billing-refunds", "1.0", "refundbeforecommit clause")
    )
    service.sync(occurred_at="2026-06-01T00:00:00Z")
    events = catalog.timeline()
    write_policy(source_dir, "billing-refunds", "2.0", "refundaftercommit clause")

    def fail_after_mutations(_connection):
        raise RuntimeError("injected ingestion failure")

    with pytest.raises(RuntimeError, match="injected ingestion failure"):
        service.sync(
            occurred_at="2026-06-02T00:00:00Z",
            before_commit=fail_after_mutations,
        )

    assert catalog.current("billing-refunds")["version"] == "1.0"
    assert catalog.timeline() == events
    assert [item.text for item in index.search("refundbeforecommit", department_id="billing")] == [
        "refundbeforecommit clause"
    ]
    assert index.search("refundaftercommit", department_id="billing") == []
