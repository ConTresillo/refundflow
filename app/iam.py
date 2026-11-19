from __future__ import annotations

from typing import Literal, Protocol

import casbin
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Permission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")
    description: str = Field(min_length=1, max_length=240)


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    tenant_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]*$")
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


_CASBIN_MODEL = """
[request_definition]
r = sub, dom, obj, act, scope, category

[policy_definition]
p = sub, kind, dom, obj, act, scope, category

[role_definition]
g = _, _, _
g2 = _, _
g3 = _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = g3(r.sub, r.dom) && r.dom == p.dom && r.obj == p.obj && r.act == p.act && r.scope == p.scope && (r.category == \"\" || r.category == p.category) && (r.sub == p.sub || g(r.sub, p.sub, r.dom)) && (p.kind != \"role\" || g2(p.sub, r.act))
"""


class AuthorizationPort(Protocol):
    def authorize(
        self, principal: Principal, document: Document, request: AccessRequest
    ) -> bool: ...


class CasbinAuthorizationAdapter:
    """Build one synchronized PyCasbin enforcer from an immutable IAM snapshot."""

    def __init__(
        self,
        roles: tuple[Role, ...],
        *,
        principals: tuple[Principal, ...],
        documents: tuple[Document, ...],
    ) -> None:
        role_keys = [(role.tenant_id, role.id) for role in roles]
        self._roles = {key: role for key, role in zip(role_keys, roles)}
        self._principals = {principal.user_id: principal for principal in principals}
        self._documents = {
            (document.tenant_id, document.id): document for document in documents
        }
        if len(self._roles) != len(roles):
            raise ValueError("role IDs must be unique within a tenant")
        if len(self._principals) != len(principals):
            raise ValueError("principal user IDs must be unique")
        if len(self._documents) != len(documents):
            raise ValueError("document IDs must be unique")

        model = casbin.Model()
        model.load_model_from_text(_CASBIN_MODEL)
        self._enforcer = casbin.SyncedEnforcer(model)
        self._load_snapshot()

    def authorize(
        self, principal: Principal, document: Document, request: AccessRequest
    ) -> bool:
        known_principal = self._principals.get(principal.user_id)
        stored_document = self._documents.get((document.tenant_id, document.id))
        if (
            known_principal is None
            or known_principal != principal
            or stored_document is None
            or stored_document != document
        ):
            return False
        return bool(
            self._enforcer.enforce(
                f"user:{known_principal.user_id}",
                f"tenant:{stored_document.tenant_id}",
                f"document:{stored_document.id}",
                request.action,
                request.scope,
                request.category or "",
            )
        )

    def _load_snapshot(self) -> None:
        for (tenant_id, role_id), role in self._roles.items():
            subject = f"role:{tenant_id or 'global'}:{role_id}"
            for permission in role.permissions:
                self._enforcer.add_named_grouping_policy(
                    "g2", subject, permission
                )

        for principal in self._principals.values():
            user_subject = f"user:{principal.user_id}"
            for membership in principal.memberships:
                domain = f"tenant:{membership.tenant_id}"
                self._enforcer.add_named_grouping_policy("g3", user_subject, domain)
                for group_id in membership.group_ids:
                    self._enforcer.add_named_grouping_policy(
                        "g", user_subject, f"group:{group_id}", domain
                    )
                for role_id in membership.role_ids:
                    role = self._roles.get((membership.tenant_id, role_id))
                    if role is None:
                        role = self._roles.get((None, role_id))
                    if role is not None:
                        self._enforcer.add_named_grouping_policy(
                            "g", user_subject, f"role:{role.tenant_id or 'global'}:{role_id}", domain
                        )

        for document in self._documents.values():
            domain = f"tenant:{document.tenant_id}"
            resource = f"document:{document.id}"
            for grant in document.grants:
                subject = f"{grant.subject_type}:{grant.subject_id}"
                if grant.subject_type == "role":
                    role = self._roles.get((document.tenant_id, grant.subject_id))
                    if role is None:
                        role = self._roles.get((None, grant.subject_id))
                    if role is None or grant.permission not in role.permissions:
                        continue
                    subject = f"role:{role.tenant_id or 'global'}:{grant.subject_id}"
                self._enforcer.add_policy(
                    subject,
                    grant.subject_type,
                    domain,
                    resource,
                    grant.permission,
                    document.scope,
                    document.category,
                )
