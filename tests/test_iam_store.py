import pytest

from app.iam import Membership, Principal
from app.iam_store import SQLiteIAMStore


def test_groups_are_tenant_scoped_idempotent_and_audited(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_group("northstar", "billing", occurred_at="t1")
    store.put_group("northstar", "billing", occurred_at="t2")
    store.put_group("southwind", "billing", occurred_at="t3")

    assert store.list_groups("northstar") == ("billing",)
    assert store.list_groups("southwind") == ("billing",)
    assert store.list_groups("unknown") == ()
    events = store.audit_events()
    assert [event["action"] for event in events] == ["group.created", "group.created"]
    assert events[0]["details_json"] == '{"tenant_id": "northstar"}'


def test_group_requires_nonempty_identifiers(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    with pytest.raises(ValueError):
        store.put_group("", "billing")
    assert store.audit_events() == []


def test_store_persists_authoritative_membership_and_refreshes_revocation(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_principal(
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar", group_ids=("billing",)),),
        )
    )

    assert store.get_principal("alice") == Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="northstar", group_ids=("billing",)),),
    )

    store.put_principal(Principal(user_id="alice", memberships=()))
    assert store.get_principal("alice") == Principal(user_id="alice", memberships=())
    assert store.list_principals() == (Principal(user_id="alice", memberships=()),)


def test_delete_principal_revokes_memberships_and_records_audit(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_principal(Principal(user_id="alice", memberships=()))

    assert store.delete_principal("alice", occurred_at="2026-11-17T10:00:00Z") is True
    assert store.get_principal("alice") is None
    assert store.delete_principal("alice", occurred_at="2026-11-17T10:01:00Z") is False
    events = store.audit_events()
    assert [event["action"] for event in events] == [
        "principal.updated",
        "principal.deleted",
    ]
    assert events[-1]["subject_id"] == "alice"
    assert events[-1]["occurred_at"] == "2026-11-17T10:00:00Z"


def test_mutations_are_audited_and_failed_mutation_leaves_no_partial_record(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_principal(Principal(user_id="alice", memberships=()))
    events = store.audit_events()
    assert len(events) == 1
    assert events[0]["action"] == "principal.updated"
    assert events[0]["subject_id"] == "alice"

    with pytest.raises(ValueError):
        store.put_principal(Principal(user_id="bad space", memberships=()))
    assert store.audit_events() == events
