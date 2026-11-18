from app.incremental_index import SQLitePolicyIndex


def chunk(policy_id: str, text: str, *, version: str = "1", department: str = "billing"):
    return {
        "policy_id": policy_id,
        "version": version,
        "department_id": department,
        "section_id": "rules",
        "text": text,
    }


def test_search_filters_allowed_policy_ids_before_global_rank_and_limit(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.sqlite3")
    index.ingest(
        [
            chunk("authorized", "refund receipt common"),
            chunk("forbidden", "refund receipt common"),
            chunk("other", "refund receipt common"),
        ]
    )

    results = index.search(
        "refund receipt common",
        department_id="billing",
        allowed_policy_ids={"authorized"},
        top_k=1,
    )

    assert [item.policy_id for item in results] == ["authorized"]


def test_empty_allowed_policy_set_returns_nothing_even_with_matching_rows(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.sqlite3")
    index.ingest([chunk("secret", "distinctive authorized evidence")])

    assert index.search(
        "distinctive authorized evidence",
        department_id="billing",
        allowed_policy_ids=set(),
        top_k=5,
    ) == []


def test_forbidden_matches_more_than_twenty_times_top_k_do_not_fill_results(tmp_path):
    index = SQLitePolicyIndex(tmp_path / "index.sqlite3")
    rows = [chunk("allowed", "needle evidence")]
    rows.extend(
        chunk(f"forbidden-{number}", "needle evidence " * (number + 1))
        for number in range(30)
    )
    index.ingest(rows)

    results = index.search(
        "needle evidence",
        department_id="billing",
        allowed_policy_ids={"allowed"},
        top_k=1,
    )

    assert [item.policy_id for item in results] == ["allowed"]
