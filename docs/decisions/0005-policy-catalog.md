# Run record 0005 — persistent versioned policy catalog

- Date/run: 2026-11-16; scheduled run (unique scheduler run ID unavailable).
- Goal: add the first persistent policy version catalog and publication timeline.
- Context: clean `main` at `533dedb41646f4b638f9c2406cd39f3f1874da6a`, matching fetched `origin/main`. An existing frontend `npm run dev` remained running and was not touched. Python environment provides SQLite, pytest, LangGraph and MCP; no existing catalog implementation was found.
- Alternatives: add a dependency-backed database or introduce a remote service; chose the Python standard-library SQLite adapter for local durability and replaceability without dependencies or network access.
- Decision/consequences: `PolicyCatalog` stores immutable (policy ID, version) snapshots and ordered publication events, rejects duplicate versions and inactive publication, and reads the most recently inserted version as current. Timestamps are caller-supplied strings for deterministic replay. This slice does not implement amend/withdraw/expire, scheduling, effective-date activation, policy-set selections, or integration with ingestion/retrieval.
- TDD: wrote two catalog behavior tests first; observed collection failure because `app.policy_catalog` did not exist. Implemented minimal SQLite catalog; focused tests then passed.
- Files: `app/policy_catalog.py`, `tests/test_policy_catalog.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none added; `.venv/Scripts/python.exe`.
- Tests: `.venv/Scripts/python.exe -m pytest tests/test_policy_catalog.py -q` — 2 passed. Full `.venv/Scripts/python.exe -m pytest -q` — 8 passed; 13 pre-existing LangSmith asyncio deprecation warnings. `git diff --check` clean.
- Security/artifact review: local SQLite only; SQL uses bound parameters; no credentials/network access or generated database committed. Existing frontend dev process left unchanged.
- Commit SHA(s): `d33797225e4aff5c8aca042d080120fbdb831454` (`Add persistent versioned policy catalog`).
- Remote HEAD: `d33797225e4aff5c8aca042d080120fbdb831454`, verified via `git ls-remote origin refs/heads/main`.
- Blocker: none for this milestone.
- Next: implement deterministic amendment/withdrawal/expiry events against the catalog, keeping history immutable and testing event timelines and current-version behavior.
