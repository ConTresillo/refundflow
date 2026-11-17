import pytest

from app.iam import Membership, Principal
from app.iam_store import SQLiteIAMStore


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
