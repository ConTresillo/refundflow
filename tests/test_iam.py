import pytest
from pydantic import ValidationError

from app.iam import (
    AccessRequest,
    AuthorizationEngine,
    Document,
    DocumentGrant,
    Membership,
    Permission,
    Principal,
    Role,
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
        grants=(DocumentGrant(subject_type="role", subject_id="reader", permission="document.read"),),
    )
    engine = AuthorizationEngine((Role(id="reader", permissions=("document.read",)),))

    assert engine.authorize(principal, document, AccessRequest(action="document.read", scope="department:billing", category="billing-policy"))


def test_authorization_denies_by_default_and_separates_category_scope_and_permission():
    principal = Principal(user_id="alice", memberships=(Membership(tenant_id="northstar"),))
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(),
    )
    engine = AuthorizationEngine(())

    assert not engine.authorize(principal, document, AccessRequest(action="document.read", scope="department:billing"))
    assert not engine.authorize(
        principal, document, AccessRequest(action="document.read", scope="department:billing", category="support-policy")
    )
    assert not engine.authorize(principal, document, AccessRequest(action="document.read", scope="department:support"))


def test_cross_tenant_grant_cannot_override_tenant_boundary():
    principal = Principal(user_id="alice", memberships=(Membership(tenant_id="other", group_ids=("shared",)),))
    document = Document(
        id="refund-policy",
        tenant_id="northstar",
        owner_user_id="bob",
        category="billing-policy",
        scope="department:billing",
        grants=(DocumentGrant(subject_type="group", subject_id="shared", permission="document.read"),),
    )
    engine = AuthorizationEngine(())

    assert not engine.authorize(principal, document, AccessRequest(action="document.read", scope="department:billing"))


def test_invalid_permission_and_duplicate_memberships_are_rejected():
    with pytest.raises(ValidationError):
        Permission(id="not-a-permission", description="invalid dotted permission")
    with pytest.raises(ValidationError):
        Principal(
            user_id="alice",
            memberships=(Membership(tenant_id="northstar"), Membership(tenant_id="northstar")),
        )
