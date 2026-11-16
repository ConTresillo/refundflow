# Run record 0006 — policy withdrawal event

- Date/run: 2026-11-16; scheduled run (unique scheduler run ID unavailable).
- Goal: support an auditable withdrawal event against the current policy snapshot.
- Context: started on clean `main` at `c6814ea`; a pre-existing frontend `npm run dev` process was found and left untouched. Existing SQLite catalog persisted immutable versions and publication timeline entries but had no withdrawal transition.
- Alternatives: delete the policy/version or add a new inactive version. Chosen: preserve version rows, mark the latest snapshot withdrawn, append an ordered event with reason and caller-supplied deterministic timestamp.
- Decision/consequences: `withdraw` rejects unknown and already-inactive policies and validates event timestamp/reason. This does not yet implement amendment, scheduled events, expiry/effective-date activation or retrieval ingestion synchronization.
- TDD: added withdrawal-history behavior test first; focused run failed as expected with `AttributeError: PolicyCatalog has no attribute withdraw`. Added the minimal transition; focused tests then passed.
- Files: `app/policy_catalog.py`, `tests/test_policy_catalog.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none added; existing `.venv/Scripts/python.exe`.
- Tests: focused catalog tests 4 passed; full suite 10 passed; 13 LangSmith asyncio deprecation warnings. `git diff --check` clean.
- Security/artifact review: withdrawal writes use SQLite bound parameters; no external services or credentials used; no generated database/cache files staged.
- Commit SHA(s): pending.
- Remote HEAD: pending verification after push.
- Blocker: none for this slice.
- Next: complete deterministic policy change simulation: amend/publish new versions, expiry, controlled scheduled effects, and test timeline/application behavior.
