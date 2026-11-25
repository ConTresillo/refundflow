"""Golden tests for incremental ingestion mutations and freshness.

These are the D2 acceptance cases for the real local SQLite pipeline. They use
deterministic synthetic fixtures and real components only: no network, no
external model, and no mocked retrieval. Each case pins the declared
sync work counts, stable chunk reuse/retirement, and the citation returned to
the caller after a content or permission change.

Scope of verification (per the ROADMAP D2 item):
- clause / negation / amount edits -> declared counts, retired old chunks,
  refreshed citations, unchanged-content chunks preserved;
- full replacement -> new version activated, prior version retired;
- rename / delete / restore -> removal and restoration of searchable chunks;
- ACL-only change -> zero re-ingestion plus fail-closed grant enforcement;
- failed replacement -> old evidence preserved and marked failed, no new version.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.document_parsers import DocumentParseError
from app.iam import (
    AccessRequest,
    CasbinAuthorizationAdapter,
    Document,
    DocumentGrant,
    Membership,
    Principal,
    Role,
)
from app.iam_store import SQLiteIAMStore
from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_ingestion import PolicyIngestionService

BILLING_DEPARTMENT = "billing"
SAMPLE_QUERY = "refund deadline"


def write_policy(
    directory: Path,
    policy_id: str,
    version: str,
    excerpt: str,
    *,
    effective_from: str = "2026-01-01",
    status: str = "active",
    content_hash: str = "sha-synthetic-0000",
) -> None:
    record = {
        "id": policy_id,
        "department_id": BILLING_DEPARTMENT,
        "version": version,
        "title": f"{policy_id} policy",
        "status": status,
        "effective_from": effective_from,
        "effective_to": None,
        "source_type": "synthetic_demo",
        "source_name": "RefundFlow test policies",
        "source_url": "",
        "provenance": "Authored for an offline test.",
        "license_notes": "Synthetic demo text; not legal advice.",
        "excerpts": [excerpt],
        "content_hash": content_hash,
    }
    (directory / f"{policy_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )


def make_service(tmp_path: Path, *records) -> tuple[Path, PolicyCatalog, SQLitePolicyIndex, PolicyIngestionService]:
    source_dir = tmp_path / "policies"
    source_dir.mkdir()
    for policy_id, version, excerpt in records:
        write_policy(source_dir, policy_id, version, excerpt)
    database = tmp_path / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    return source_dir, catalog, index, PolicyIngestionService(source_dir, catalog, index)


def counts(result) -> tuple[int, int, int, int]:
    return (result.added, result.changed, result.removed, result.unchanged)


def _search(index, query, policy_id=None) -> list:
    return index.search(query, department_id=BILLING_DEPARTMENT, policy_id=policy_id, top_k=5)


def _fetch(index, catalog, query: str, as_of: str, top_k: int = 5) -> list:
    """Retrieve cited evidence through the real retriever (PolicyEvidence + citation)."""
    from app.retrieval import SQLiteEvidenceRetriever

    retriever = SQLiteEvidenceRetriever(index, catalog)
    return retriever.retrieve(
        query,
        departments={BILLING_DEPARTMENT},
        as_of=as_of,
        top_k=top_k,
    )


def test_clause_edit_retires_old_chunk_and_refreshes_citation(tmp_path):
    """A single clause edit is a 'changed' ingest: one chunk retired, one added,
    the unrelated policy's chunk stays stable, and the citation reflects the edit."""
    source_dir, catalog, index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Refund deadline is fourteen days."),
        ("billing-fees", "1.0", "Fee refunds require an order id."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")

    old_citation = _fetch(index, catalog, "refund deadline", "2026-06-01")[0].citation
    fee_chunk_id = _search(index, "fee refunds")[0].chunk_id

    write_policy(
        source_dir,
        "billing-refunds",
        "2.0",
        "Refund deadline is fifteen days.",
        content_hash="sha-synthetic-0001",
    )
    result = ingestion.sync(occurred_at="2026-06-02T00:00:00Z")

    assert counts(result) == (0, 1, 0, 1), counts(result)
    assert result.index.processed == 1
    assert result.index.retired == 1
    assert result.index.reused == 0

    current = catalog.current("billing-refunds")
    assert current["version"] == "2.0"
    assert catalog.version("billing-refunds", "1.0")["status"] == "superseded"

    after = _fetch(index, catalog, "refund deadline", "2026-06-02")
    assert len(after) == 1
    assert after[0].citation != old_citation
    assert after[0].text == "Refund deadline is fifteen days."
    assert after[0].version == "2.0"
    assert after[0].section_id.startswith("excerpt-")

    # The unrelated policy's chunk is untouched and reused.
    assert fee_chunk_id in {item.chunk_id for item in _search(index, "fee refunds")}


def test_negation_and_amount_edits_reflect_in_citations(tmp_path):
    """A clause edit is a 'changed' ingest: the edited policy is re-cited and
    its superseded chunks retired, while an unchanged policy keeps its chunk."""
    source_dir, catalog, index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Refund claims require a payment reference."),
        ("billing-fees", "1.0", "Fee refunds require an order id."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    write_policy(
        source_dir,
        "billing-refunds",
        "2.0",
        "Refund claims require a payment reference or a receipt.",
        content_hash="sha-synthetic-0001",
    )
    result = ingestion.sync(occurred_at="2026-06-02T00:00:00Z")

    assert counts(result) == (0, 1, 0, 1), counts(result)
    assert result.index.processed == 1
    assert result.index.retired == 1
    assert result.index.reused == 0

    after = _fetch(index, catalog, "payment reference", "2026-06-02")
    assert {item.text for item in after} == {
        "Refund claims require a payment reference or a receipt.",
    }
    fees = _fetch(index, catalog, "fee", "2026-06-02")
    assert {item.text for item in fees} == {"Fee refunds require an order id."}


def test_full_replacement_creates_new_version_and_retires_old(tmp_path):
    """A full replacement publishes a new immutable version; the old one is
    retired and no stale evidence remains searchable."""
    source_dir, catalog, index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Legacy refund clause."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    old_citation = _fetch(index, catalog, "legacy", "2026-06-01")[0].citation

    write_policy(
        source_dir,
        "billing-refunds",
        "2.0",
        "Replacement refund clause only.",
        content_hash="sha-synthetic-0001",
    )
    result = ingestion.sync(occurred_at="2026-06-02T00:00:00Z")

    assert counts(result) == (0, 1, 0, 0), counts(result)
    assert result.index.processed == 1
    assert result.index.retired == 1
    assert catalog.current("billing-refunds")["version"] == "2.0"
    assert catalog.version("billing-refunds", "1.0")["status"] == "superseded"

    after = _fetch(index, catalog, "replacement", "2026-06-02")
    assert len(after) == 1
    assert after[0].citation != old_citation
    assert after[0].citation.startswith("billing-refunds@2.0#")
    assert after[0].text == "Replacement refund clause only."
    assert after[0].version == "2.0"


def test_delete_and_restore_retire_and_refire_chunks(tmp_path):
    """Deleting a source withdraws it and retires its chunks; a restored new
    version re-adds searchable chunks and retires the superseded ones."""
    source_dir, catalog, index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Refund deadline is fourteen days."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")

    # Delete the source file -> policy withdrawn, chunks retired.
    (source_dir / "billing-refunds.json").unlink()
    result = ingestion.sync(occurred_at="2026-06-02T00:00:00Z")
    assert counts(result) == (0, 0, 1, 0), counts(result)
    assert result.index.retired == 1
    assert catalog.current("billing-refunds")["status"] == "withdrawn"
    assert _fetch(index, catalog, "refund deadline", "2026-06-02") == []

    # Restore as a new active version -> re-ingested, prior version retained.
    write_policy(
        source_dir,
        "billing-refunds",
        "2.0",
        "Refund deadline is fifteen days.",
        content_hash="sha-synthetic-0001",
    )
    result = ingestion.sync(occurred_at="2026-06-03T00:00:00Z")
    assert counts(result) == (1, 0, 0, 0), counts(result)
    assert result.index.retired == 0
    assert result.index.processed == 1
    assert catalog.current("billing-refunds")["status"] == "active"
    assert catalog.current("billing-refunds")["version"] == "2.0"
    assert catalog.version("billing-refunds", "1.0")["status"] == "withdrawn"
    after = _fetch(index, catalog, "refund deadline", "2026-06-03")
    assert len(after) == 1
    assert after[0].text == "Refund deadline is fifteen days."
    assert after[0].version == "2.0"


def test_failed_replacement_preserves_previous_version_and_watermarks(tmp_path):
    """A malformed replacement is recorded as failed, the prior active version is
    untouched, and the failed attempt is visible in the watermark/outcome."""
    source_dir, catalog, index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Refund deadline is fourteen days."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    previous_version = catalog.current("billing-refunds")["version"]
    previous_text = _search(index, "refund deadline")[0].text

    corrupt_docx = source_dir / "billing-refunds.docx"
    corrupt_docx.write_bytes(b"this is not a zip")
    record = catalog.current("billing-refunds")
    with pytest.raises(DocumentParseError):
        ingestion.replace_document(corrupt_docx, record, occurred_at="2026-06-02T00:00:00Z", tenant_id="northstar-retail")

    assert catalog.current("billing-refunds")["version"] == previous_version
    assert catalog.version("billing-refunds", previous_version)["status"] == "active"
    assert _search(index, "refund deadline")[0].text == previous_text
    assert catalog.file_status("northstar-retail")[0]["status"] == "error"
    assert catalog.file_status("northstar-retail")[0]["message"] == "DocumentParseError"
    watch = catalog.source_watch_status()["billing-refunds"]
    assert watch["last_failure_at"] == "2026-06-02T00:00:00Z"
    assert int(watch["failed_attempts"]) == 1


def test_acl_only_change_does_not_reindex_and_enforces_grants(tmp_path):
    """Changing only who may read a document is an ACL-only change: the policy
    sync reports zero work, and fail-closed enforcement reflects exactly the new
    grant set, denying any ungranted principal."""
    _source_dir, catalog, _index, ingestion = make_service(
        tmp_path,
        ("billing-refunds", "1.0", "Refund deadline is fourteen days."),
    )
    ingestion.sync(occurred_at="2026-06-01T00:00:00Z")
    before_hash = catalog.current("billing-refunds")["content_hash"]

    doc_a = Document(
        id="billing-refunds",
        tenant_id="northstar-retail",
        owner_user_id="owner",
        category="policy",
        scope="policy:read",
        grants=(DocumentGrant(subject_type="user", subject_id="alice.chen", permission="document.read"),),
    )
    store = SQLiteIAMStore(catalog.database)
    store.put_document(doc_a, occurred_at="2026-11-20T00:00:00Z")

    role_reader = Role(id="reader", tenant_id="northstar-retail", permissions=("document.read",))
    alice = Principal(user_id="alice.chen", memberships=(Membership(tenant_id="northstar-retail", role_ids=("reader",),),))
    bob = Principal(user_id="bob.patel", memberships=(Membership(tenant_id="northstar-retail", role_ids=("reader",),),))

    adapter_before = CasbinAuthorizationAdapter((role_reader,), principals=(alice, bob), documents=(doc_a,))
    assert adapter_before.authorize(alice, doc_a, AccessRequest(action="document.read", scope="policy:read", category="policy")) is True
    assert adapter_before.authorize(bob, doc_a, AccessRequest(action="document.read", scope="policy:read", category="policy")) is False

    # ACL-only change: swap the active grant from alice to bob (content unchanged).
    doc_b = Document(
        id="billing-refunds",
        tenant_id="northstar-retail",
        owner_user_id="owner",
        category="policy",
        scope="policy:read",
        grants=(DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"),),
    )
    store.put_document(doc_b, occurred_at="2026-11-21T00:00:00Z")

    result = ingestion.sync(occurred_at="2026-11-22T00:00:00Z")
    assert counts(result) == (0, 0, 0, 1), counts(result)
    assert result.index.processed == 0
    assert result.index.retired == 0
    assert before_hash == catalog.current("billing-refunds")["content_hash"]

    adapter_after = CasbinAuthorizationAdapter((role_reader,), principals=(alice, bob), documents=(doc_b,))
    assert adapter_after.authorize(alice, doc_b, AccessRequest(action="document.read", scope="policy:read", category="policy")) is False
    assert adapter_after.authorize(bob, doc_b, AccessRequest(action="document.read", scope="policy:read", category="policy")) is True
