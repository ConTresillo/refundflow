from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Permission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
    description: str = Field(min_length=1, max_length=240)


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    permissions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_permissions(self) -> Role:
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("role permissions must be unique")
        return self


class Membership(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    group_ids: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unique_membership_refs(self) -> Membership:
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("group memberships must be unique")
        if len(set(self.role_ids)) != len(self.role_ids):
            raise ValueError("membership roles must be unique")
        return self


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    memberships: tuple[Membership, ...]

    @model_validator(mode="after")
    def unique_tenants(self) -> Principal:
        tenant_ids = [item.tenant_id for item in self.memberships]
        if len(set(tenant_ids)) != len(tenant_ids):
            raise ValueError("principal memberships must have unique tenant IDs")
        return self


class DocumentGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_type: Literal["user", "group", "role"]
    subject_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    permission: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    owner_user_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    category: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    scope: str = Field(min_length=1, max_length=200)
    grants: tuple[DocumentGrant, ...] = ()


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
    scope: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")


class AuthorizationEngine:
    """Small local policy evaluator; unknown actions and resources fail closed."""

    def __init__(self, roles: tuple[Role, ...]) -> None:
        self._roles = {role.id: role for role in roles}
        if len(self._roles) != len(roles):
            raise ValueError("role IDs must be unique")

    def authorize(
        self, principal: Principal, document: Document, request: AccessRequest
    ) -> bool:
        if request.action != "document.read" or request.scope != document.scope:
            return False
        if request.category is not None and request.category != document.category:
            return False
        membership = next(
            (item for item in principal.memberships if item.tenant_id == document.tenant_id),
            None,
        )
        if membership is None:
            return False
        subjects = {("user", principal.user_id)}
        subjects.update(("group", group_id) for group_id in membership.group_ids)
        subjects.update(("role", role_id) for role_id in membership.role_ids)
        effective_permissions = {
            permission
            for role_id in membership.role_ids
            if role_id in self._roles
            for permission in self._roles[role_id].permissions
        }
        return any(
            (grant.subject_type, grant.subject_id) in subjects
            and grant.permission == request.action
            and (
                grant.subject_type != "role"
                or grant.permission in effective_permissions
            )
            for grant in document.grants
        )
