"""Safe local policy workspace bootstrap for the opt-in demo launcher."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService


def seed_development_evidence_grants(service: PolicyChangeService) -> None:
    """Grant only synthetic billing policy reads to the seeded billing analyst."""
    from app.iam import Document, DocumentGrant
    from app.iam_store import SQLiteIAMStore

    store = SQLiteIAMStore(service._database)
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
                    (DocumentGrant(subject_type="user", subject_id="bob.patel", permission="document.read"),)
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
