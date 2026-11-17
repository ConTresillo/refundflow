import pytest
from pydantic import ValidationError

from app import iam
from app.iam import (
    AccessRequest,
    Document,
    DocumentGrant,
    Membership,
    Permission,
    Principal,
    Role,
)


def _adapter(
    roles: tuple[Role, ...],
    principal: Principal,
    document: Document,
    additional_principals: tuple[Principal, ...] = (),
    additional_documents: tuple[Document, ...] = (),
):
    adapter = getattr(iam, "CasbinAuthorizationAdapter", None)
    assert adapter is not None, "Authorization decisions must use the Casbin adapter"
    return adapter(
        roles,
        principals=(principal, *additional_principals),
        documents=(document, *additional_documents),
    )


def test_membership_role_permission_grant_allows_matching_tenant_resource():
    principal = Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="northstar", role_ids=("reader",)),),
    )
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="role", subject_id="reader", permission="document.read"
            ),
        ),
    )
    engine = _adapter(
        (Role(id="reader", permissions=("document.read",)),), principal, document
    )

    assert engine.authorize(
        principal,
        document,
        AccessRequest(
            action="document.read",
            scope="department:billing",
            category="billing-policy",
        ),
    )


def test_authorization_denies_by_default_and_separates_category_scope_and_permission():
    principal = Principal(
        user_id="alice", memberships=(Membership(tenant_id="northstar"),)
    )
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(),
    )
    engine = _adapter((), principal, document)

    assert not engine.authorize(
        principal,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )
    assert not engine.authorize(
        principal,
        document,
        AccessRequest(
            action="document.read",
            scope="department:billing",
            category="support-policy",
        ),
    )
    assert not engine.authorize(
        principal,
        document,
        AccessRequest(action="document.read", scope="department:support"),
    )


def test_cross_tenant_grant_cannot_override_tenant_boundary():
    principal = Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="other", group_ids=("shared",)),),
    )
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="group", subject_id="shared", permission="document.read"
            ),
        ),
    )
    engine = _adapter((), principal, document)

    assert not engine.authorize(
        principal,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )


def test_same_document_id_is_scoped_by_tenant():
    principal = Principal(
        user_id="alice", memberships=(Membership(tenant_id="northstar"),)
    )
    north_document = Document(
        id="shared-policy-id",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    south_document = Document(
        id="shared-policy-id",
        tenant_id="southstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    adapter = _adapter(
        (), principal, north_document, additional_documents=(south_document,)
    )
    request = AccessRequest(action="document.read", scope="department:billing")

    assert adapter.authorize(principal, north_document, request)
    assert not adapter.authorize(principal, south_document, request)


def test_invalid_permission_and_duplicate_memberships_are_rejected():
    with pytest.raises(ValidationError):
        Permission(id="not-a-permission", description="invalid dotted permission")
    with pytest.raises(ValidationError):
        Principal(
            user_id="alice",
            memberships=(
                Membership(tenant_id="northstar"),
                Membership(tenant_id="northstar"),
            ),
        )


def test_group_grant_allows_current_member_in_the_same_tenant():
    principal = Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="northstar", group_ids=("billing",)),),
    )
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="group", subject_id="billing", permission="document.read"
            ),
        ),
    )

    assert _adapter((), principal, document).authorize(
        principal,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )


def test_direct_user_grant_does_not_leak_to_another_user():
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    request = AccessRequest(action="document.read", scope="department:billing")
    alice = Principal(user_id="alice", memberships=(Membership(tenant_id="northstar"),))
    bob = Principal(user_id="bob", memberships=(Membership(tenant_id="northstar"),))
    adapter = _adapter((), alice, document, (bob,))

    assert adapter.authorize(alice, document, request)
    assert not adapter.authorize(bob, document, request)


def test_direct_user_grant_requires_current_tenant_membership():
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    principal_without_membership = Principal(user_id="alice", memberships=())

    assert not _adapter((), principal_without_membership, document).authorize(
        principal_without_membership,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )


def test_request_principal_cannot_forge_snapshot_membership():
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    snapshot_principal = Principal(user_id="alice", memberships=())
    forged_principal = Principal(
        user_id="alice", memberships=(Membership(tenant_id="northstar"),)
    )

    assert not _adapter((), snapshot_principal, document).authorize(
        forged_principal,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )


def test_caller_supplied_document_must_match_trusted_snapshot():
    principal = Principal(
        user_id="alice", memberships=(Membership(tenant_id="northstar"),)
    )
    stored_document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="user", subject_id="alice", permission="document.read"
            ),
        ),
    )
    forged_document = Document(
        id="refund-policy",
        tenant_id="other",
        owner_user_id="bob",
        category="support-policy",
        scope="department:support",
        grants=(),
    )

    assert not _adapter((), principal, stored_document).authorize(
        principal,
        forged_document,
        AccessRequest(action="document.read", scope="department:billing"),
    )


def test_role_without_requested_permission_is_denied():
    principal = Principal(
        user_id="alice",
        memberships=(Membership(tenant_id="northstar", role_ids=("reader",)),),
    )
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(
            DocumentGrant(
                subject_type="role", subject_id="reader", permission="document.read"
            ),
        ),
    )

    assert not _adapter((Role(id="reader"),), principal, document).authorize(
        principal,
        document,
        AccessRequest(action="document.read", scope="department:billing"),
    )
