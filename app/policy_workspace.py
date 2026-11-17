"""Safe local policy workspace bootstrap for the opt-in demo launcher."""

from __future__ import annotations

import shutil
from pathlib import Path

from app.incremental_index import SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog
from app.policy_change_service import PolicyChangeService


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
        initial_sources = Path(seed_dir) if seed_dir is not None else Path(__file__).with_name("policies")
        files = sorted(initial_sources.glob("*.json"))
        if not files:
            raise FileNotFoundError(f"no seed policy documents found in {initial_sources}")
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
