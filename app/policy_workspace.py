"""Safe local policy workspace bootstrap for the opt-in demo launcher."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Callable

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService


def seed_development_evidence_grants(service: PolicyChangeService) -> None:
    """Grant only synthetic billing policy reads to the seeded billing analyst."""
    from app.iam import Document, DocumentGrant, Membership, Principal
    from app.iam_store import SQLiteIAMStore
    from app.identity import load_seeded_identities

    store = SQLiteIAMStore(service._database)
    for seeded in load_seeded_identities():
        group_id = "billing-analysts" if seeded.subject_id == "bob.patel" else ("risk-reviewers" if seeded.subject_id == "dana.ross" else "support-agents")
        store.put_group(seeded.tenant_id, group_id, occurred_at="development-demo-fixture")
        store.put_principal(Principal(user_id=seeded.subject_id, memberships=(Membership(tenant_id=seeded.tenant_id, group_ids=(group_id,)),)), occurred_at="development-demo-fixture")


    existing = {
        (item.tenant_id, item.id)
        for item in store.authorization_document_snapshot()[1]
    }
    for record in service.list_policies():
        policy_id = record["policy_id"]
        department_id = record["department_id"]
        if ("northstar-retail", policy_id) in existing:
            continue
        store.put_document(
            Document(
                id=policy_id,
                tenant_id="northstar-retail",
                owner_user_id="bob.patel" if department_id == "billing" else "alice.chen",
                category=f"{department_id}-policy",
                scope=f"department:{department_id}",
                grants=(
                    (
                        DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"),
                        *((DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.write"),) if policy_id == "billing-refunds" else ()),
                    )
                    if department_id == "billing" else ()
                ),
            ),
            occurred_at="development-demo-fixture",
        )


def create_local_policy_service(
    data_dir: str | Path,
    *,
    seed_dir: str | Path | None = None,
) -> PolicyChangeService:
    """Create a persistent local copy without overwriting existing user policies."""
    root = Path(data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    source_dir = root / "policies"
    if not source_dir.exists():
        source_dir.mkdir()
        initial_sources = (
            Path(seed_dir)
            if seed_dir is not None
            else Path(__file__).with_name("policies")
        )
        files = sorted(initial_sources.glob("*.json"))
        if not files:
            raise FileNotFoundError(
                f"no seed policy documents found in {initial_sources}"
            )
        for source in files:
            shutil.copyfile(source, source_dir / source.name)
    elif not source_dir.is_dir():
        raise NotADirectoryError(f"policy workspace is not a directory: {source_dir}")

    database = root / "refundflow.sqlite3"
    catalog = PolicyCatalog(database)
    index = SQLitePolicyIndex(database)
    service = PolicyChangeService(source_dir, catalog, index)
    service.initialize_empty_database()
    return service


def build_evidence_scope(policy_change_service: PolicyChangeService) -> Callable[..., set[str]]:
    """Fail-closed evidence scope: what the actor may read, per tenant.

    Mirrors the synchronous endpoints' grant logic (tenant-qualified PyCasbin
    plus document-read grants over non-colliding tenant policies). Returns the
    allowed policy IDs for the run; an empty result makes the graph deny the
    run without touching retrieval.
    """
    from app.iam import AccessRequest, CasbinAuthorizationAdapter, Role
    from app.iam_store import SQLiteIAMStore

    iam_store = SQLiteIAMStore(policy_change_service._database)

    def scope(*, tenant_id: str, actor_subject_id: str | None, departments: tuple[str, ...]) -> set[str]:
        if not tenant_id or not actor_subject_id:
            return set()
        principal = iam_store.get_principal(actor_subject_id)
        if principal is None:
            return set()
        _revision, documents = iam_store.authorization_document_snapshot()
        tenant_documents = {document.id: document for document in documents if document.tenant_id == tenant_id}
        if not tenant_documents:
            return set()
        tenant_owners: dict[str, set[str]] = {}
        for document in documents:
            tenant_owners.setdefault(document.id, set()).add(document.tenant_id)
        colliding = {document_id for document_id, owners in tenant_owners.items() if len(owners) > 1}
        roles = tuple(
            Role(
                id=role,
                tenant_id=tenant_id,
                permissions=iam_store.list_role_permissions(tenant_id, role),
            )
            for role in iam_store.list_roles(tenant_id)
        )
        adapter = CasbinAuthorizationAdapter(
            roles,
            principals=(principal,),
            documents=tuple(tenant_documents.values()),
        )
        allowed = set()
        for document_id, document in tenant_documents.items():
            if document_id in colliding:
                continue
            if adapter.authorize(
                principal,
                document,
                AccessRequest(action="document.read", scope=document.scope, category=document.category),
            ):
                allowed.add(document_id)
        return allowed

    return scope
