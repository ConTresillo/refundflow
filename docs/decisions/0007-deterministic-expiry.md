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
- Commit SHA: `8f9eb8b0e183ecc685426ef552f18899a0eda6d0`.
- Remote verification: that commit is an ancestor of `origin/main` at `a9f6b48e5f8bad81c77bc9af71fa6a652cdab609` (verified 2026-11-16).
- Blocker: none for this milestone.
- Next: scheduled publish/amendment activation and ingestion/retrieval freshness synchronization, starting with data contract and tests.
## Run record 0008 — detailed LLD design

- Date/run: 2026-11-16; manual cron invocation (scheduler run ID unavailable).
- Goal: audit current design and expand LLD with implementable contracts/diagrams.
- Baseline: `8f9eb8b0e183ecc685426ef552f18899a0eda6d0`; HLD retained. Catalog and lexical retrieval exist; graph/MCP/index/API/UI do not.
- Decision: expand LLD before code; it lacked schema/concurrency, lifecycle precedence, HITL and failure/security contracts. Retain SQLite, proportional ports, local MCP only; approval is simulation annotation.
- Sources: official SQLite transaction/FK, LangGraph persistence/interrupts, MCP architecture, Docling loader; links in LLD. Citation-ledger helper unavailable.
- Files: LLD, roadmap, this record. No dependency/runtime changes.
- Verification: `.venv/Scripts/python.exe -m pytest -q` → 11 passed; 13 existing LangSmith deprecation warnings. `git diff --check` passed. Six Mermaid diagram fences and expected types checked; grammar validation unavailable (no renderer installed).
- Security/artifacts: documentation-only; no secrets or runtime changes.
- Commit SHA: `a9f6b48e5f8bad81c77bc9af71fa6a652cdab609`; verified remote `main` matched at this SHA after push.
- Next: tests-first SQLite incremental index, stable IDs and unchanged/changed/deleted/failure cases.
