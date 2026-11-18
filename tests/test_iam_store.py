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


def test_roles_are_tenant_scoped_idempotent_and_audited(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_role("northstar", "reviewer", occurred_at="t1")
    store.put_role("northstar", "reviewer", occurred_at="t2")
    store.put_role("southwind", "reviewer", occurred_at="t3")

    assert store.list_roles("northstar") == ("reviewer",)
    assert store.list_roles("southwind") == ("reviewer",)
    assert store.list_roles("unknown") == ()
    events = store.audit_events()
    assert [event["action"] for event in events] == ["role.created", "role.created"]
    assert events[0]["details_json"] == '{"tenant_id": "northstar"}'

    with pytest.raises(ValueError):
        store.put_role("northstar", " ")
    assert len(store.audit_events()) == 2


def test_role_permission_assignments_are_tenant_scoped_idempotent_and_audited(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_role("northstar", "reviewer")
    store.put_permission("northstar", "document.read")
    store.put_role("southwind", "reviewer")
    store.put_permission("southwind", "document.write")

    store.assign_role_permission("northstar", "reviewer", "document.read", occurred_at="t1")
    store.assign_role_permission("northstar", "reviewer", "document.read", occurred_at="t2")
    store.assign_role_permission("southwind", "reviewer", "document.write", occurred_at="t3")

    assert store.list_role_permissions("northstar", "reviewer") == ("document.read",)
    assert store.list_role_permissions("southwind", "reviewer") == ("document.write",)
    assert store.list_role_permissions("northstar", "missing") == ()
    assignments = [event for event in store.audit_events() if event["action"] == "role.permission.assigned"]
    assert len(assignments) == 2
    assert assignments[0]["details_json"] == '{"permission_id": "document.read", "tenant_id": "northstar"}'


def test_role_permission_assignment_rejects_missing_or_cross_tenant_refs(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_role("northstar", "reviewer")
    store.put_permission("southwind", "document.read")

    with pytest.raises(ValueError, match="permission does not belong"):
        store.assign_role_permission("northstar", "reviewer", "document.read")
    with pytest.raises(ValueError, match="role does not belong"):
        store.assign_role_permission("northstar", "other", "document.read")
    assert store.list_role_permissions("northstar", "reviewer") == ()
    assert not any(event["action"] == "role.permission.assigned" for event in store.audit_events())


def test_permissions_are_tenant_scoped_idempotent_and_audited(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_permission("northstar", "document.read", occurred_at="t1")
    store.put_permission("northstar", "document.read", occurred_at="t2")
    store.put_permission("southwind", "document.read", occurred_at="t3")

    assert store.list_permissions("northstar") == ("document.read",)
    assert store.list_permissions("southwind") == ("document.read",)
    assert store.list_permissions("unknown") == ()
    events = store.audit_events()
    assert [event["action"] for event in events] == ["permission.created", "permission.created"]
    assert events[0]["details_json"] == '{"tenant_id": "northstar"}'

    with pytest.raises(ValueError):
        store.put_permission("northstar", " ")
    assert len(store.audit_events()) == 2


def test_role_snapshot_loads_only_complete_tenant_roles_for_casbin(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_role("northstar", "reviewer")
    store.put_role("southwind", "reviewer")
    store.put_permission("northstar", "document.read")
    store.put_permission("southwind", "document.write")
    store.put_principal(
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar", role_ids=("reviewer",)),),
        )
    )

    assert store.authorization_snapshot() == (
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar", role_ids=("reviewer",)),),
        ),
    )

    store.put_role("northstar", "unassigned")
    assert store.authorization_snapshot()[0].user_id == "alice"


def test_roles_with_missing_permissions_are_not_authorized_snapshot_entries(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.sqlite3")
    store.put_role("northstar", "reviewer")
    store.put_principal(
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar", role_ids=("reviewer",)),),
        )
    )

    assert store.authorization_snapshot() == (
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar", role_ids=("reviewer",)),),
        ),
    )


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


def test_authorization_principal_snapshot_pairs_revision_and_memberships(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.db")
    principal = Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="northstar", role_ids=("reviewer",)),),
    )
    store.put_principal(principal, occurred_at="t1")

    revision, principals = store.authorization_principal_snapshot()

    assert revision == store.authorization_revision() == 1
    assert principals == (principal,)


def test_authorization_revision_advances_only_for_committed_principal_changes(tmp_path):
    store = SQLiteIAMStore(tmp_path / "iam.db")

    assert store.authorization_revision() == 0
    store.put_principal(Principal(user_id="alice", memberships=()), occurred_at="t1")
    assert store.authorization_revision() == 1
    store.put_principal(Principal(user_id="alice", memberships=()), occurred_at="t2")
    assert store.authorization_revision() == 2
    assert store.delete_principal("missing", occurred_at="t3") is False
    assert store.authorization_revision() == 2
    assert store.delete_principal("alice", occurred_at="t4") is True
    assert store.authorization_revision() == 3
    assert SQLiteIAMStore(tmp_path / "iam.db").authorization_revision() == 3


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
