import sqlite3

import pytest

from app.incremental_index import ChunkInput, SQLitePolicyIndex


def make_chunk(
    text: str,
    *,
    policy_id: str = "p1",
    version: str = "1",
    department_id: str = "billing",
    section_id: str = "a",
) -> ChunkInput:
    return {
        "policy_id": policy_id,
        "version": version,
        "department_id": department_id,
        "section_id": section_id,
        "text": text,
    }


def test_unchanged_chunk_ids_and_content_are_reused(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    first = index.ingest(
        [
            make_chunk("refund   timing", section_id="a"),
            make_chunk("payment evidence", section_id="b"),
        ]
    )
    second = index.ingest(
        [
            make_chunk("refund timing", section_id="a"),
            make_chunk("payment evidence", section_id="b"),
        ]
    )

    assert first.processed == 2
    assert second.processed == 0
    assert second.reused == 2
    assert [item.chunk_id for item in first.chunks] == [
        item.chunk_id for item in second.chunks
    ]
    assert first.chunks[0].text == "refund timing"
    assert index.search("refund", department_id="billing")[0].text == "refund timing"


def test_ingest_rejects_mixed_departments_for_one_policy_version(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")

    with pytest.raises(ValueError, match="one department"):
        index.ingest(
            [
                make_chunk("billing rule", department_id="billing"),
                make_chunk("support rule", department_id="support"),
            ]
        )


def test_ingest_rejects_department_change_for_existing_policy_version(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    index.ingest([make_chunk("billing rule", department_id="billing")])

    with pytest.raises(ValueError, match="department.*immutable"):
        index.ingest([make_chunk("support rule", department_id="support")])

    assert [
        item.text for item in index.search("billing", department_id="billing")
    ] == ["billing rule"]
    assert index.search("support", department_id="support") == []


def test_ingest_failure_rolls_back_previous_index(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    index.ingest([make_chunk("old refund clause")])

    def fail_after_write(connection: sqlite3.Connection) -> None:
        raise RuntimeError("injected transaction failure")

    with pytest.raises(RuntimeError, match="injected transaction failure"):
        index.ingest(
            [make_chunk("new refund clause")],
            before_commit=fail_after_write,
        )

    results = index.search("old", department_id="billing")
    assert len(results) == 1
    assert results[0].text == "old refund clause"
    assert index.search("new", department_id="billing") == []


def test_search_matches_any_query_term(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    index.ingest([make_chunk("old refund clause")])

    results = index.search("new refund", department_id="billing")

    assert [item.text for item in results] == ["old refund clause"]


def test_multiple_chunks_can_share_a_source_section(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    result = index.ingest(
        [
            make_chunk("first passage", section_id="s1"),
            make_chunk("second passage", section_id="s1"),
        ]
    )

    assert result.processed == 2
    assert sorted(
        item.text for item in index.search("passage", department_id="billing")
    ) == ["first passage", "second passage"]


def test_changed_chunk_retires_old_content_for_same_policy_version(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    old_result = index.ingest([make_chunk("legacyphrase")])
    new_result = index.ingest([make_chunk("replacementphrase")])

    assert new_result.processed == 1
    assert new_result.retired == 1
    assert old_result.chunks[0].chunk_id != new_result.chunks[0].chunk_id
    assert index.search("legacyphrase", department_id="billing") == []
    assert index.search("replacementphrase", department_id="billing") == list(
        new_result.chunks
    )


def test_empty_complete_snapshot_retires_deleted_version(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    index.ingest([make_chunk("orphanphrase")])

    result = index.ingest([], complete_versions=[("p1", "1")])

    assert result.retired == 1
    assert index.search("orphanphrase", department_id="billing") == []


def test_search_can_scope_results_to_a_policy_version(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.db")
    index.ingest([make_chunk("policyterm historic", version="1")])
    index.ingest([make_chunk("policyterm current", version="2")])
    index.ingest(
        [make_chunk("policyterm other", policy_id="p2", version="1")]
    )

    historic = index.search(
        "policyterm", department_id="billing", policy_id="p1", version="1"
    )
    current = index.search(
        "policyterm", department_id="billing", policy_id="p1", version="2"
    )
    other_policy = index.search(
        "policyterm", department_id="billing", policy_id="p2", version="1"
    )

    assert [item.text for item in historic] == ["policyterm historic"]
    assert [item.text for item in current] == ["policyterm current"]
    assert [item.text for item in other_policy] == ["policyterm other"]
