# Run record 0007 — deterministic expiry transition

- Date/run: 2026-11-16; scheduled run (unique scheduler run ID unavailable).
- Goal: move expired current policy snapshots into a durable terminal status with an auditable, deterministic timeline event.
- Context: clean checkout at `6c3aeb2`; an existing frontend dev server was present and left untouched. The catalog supported publication and withdrawal, but had no expiry transition.
- Alternatives: infer status during reads, run against wall-clock time, or expose a caller-supplied date transition. Chosen: explicit `activate_due_changes(as_of=...)`, no hidden clock, persisted state and event.
- Consequences: expiry is repeatable and only applies after the inclusive `effective_to` date. This call does not automatically couple catalog mutation to retrieval freshness/index maintenance; future-scheduled publish/amend remains open.
- TDD: added the expiry timeline/status test first; observed expected `AttributeError` for missing method; implemented then verified.
- Files: `app/policy_catalog.py`, `tests/test_policy_catalog.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none; existing `.venv/Scripts/python.exe`.
- Tests: focused catalog tests: 5 passed. Full suite: 11 passed. LangSmith emitted 13 `asyncio.iscoroutinefunction` deprecation warnings. `git diff --check` clean.
- Security/artifact review: parameterized SQLite queries; no credentials, external calls, or generated files staged. Date parsing uses stdlib ISO dates.
- Commit SHA(s): pending.
- Remote HEAD: pending verification after push.
- Blocker: none for this milestone.
- Next: scheduled publish/amendment activation and ingestion/retrieval freshness synchronization, starting with data contract and tests.
