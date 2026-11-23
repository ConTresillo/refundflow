import json
from pathlib import Path

from app.policy_workspace import create_local_policy_service


def test_local_workspace_seeds_once_and_does_not_auto_publish_disk_edits(tmp_path):
    seed_dir = Path(__file__).parents[1] / "app" / "policies"
    data_dir = tmp_path / "refundflow-data"

    service = create_local_policy_service(data_dir, seed_dir=seed_dir)
    initial = service.list_policies()

    assert len(initial) == 4
    assert all(item["index_reflected"] for item in initial)
    target = data_dir / "policies" / "billing-refunds.json"
    original = json.loads(target.read_text(encoding="utf-8"))
    edited = {
        **original,
        "excerpts": [*original["excerpts"], "Unreviewed manual edit."],
    }
    target.write_text(json.dumps(edited), encoding="utf-8")

    reopened = create_local_policy_service(data_dir, seed_dir=seed_dir)
    policy = next(
        item
        for item in reopened.list_policies()
        if item["policy_id"] == "billing-refunds"
    )

    assert policy["version"] == original["version"]
    assert policy["source_state"] == "changed_on_disk"
    assert "Unreviewed manual edit." in policy["source_diff"]
    assert policy["index_reflected"] is True
    assert len(policy["excerpts"]) == len(original["excerpts"])
    assert "Unreviewed manual edit." in target.read_text(encoding="utf-8")
