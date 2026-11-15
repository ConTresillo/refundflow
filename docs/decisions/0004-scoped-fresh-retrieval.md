# Run record 0004 — scoped and freshness-aware retrieval

- Date/run: 2026-11-16; scheduled run (unique scheduler run ID unavailable).
- Goal: provide department/policy scoping and effective-date/status filtering in the existing retrieval surface.
- Context: prior run record 0003 completed synthetic corpus validation and explicitly identified scoped retrieval as the next slice. Worktree was clean; local `HEAD` and fetched `origin/main` both matched `2b1768211a07f03beb99867ff06be1a5c21a83f9`. An existing `npm run dev` process was observed and left undisturbed; this Python retrieval change does not overlap it.
- Alternatives: replace the lexical retriever with a new indexing subsystem now; or add optional filters to the established pure retrieval function. Chose the latter as a narrow compatible increment before persistent RAG/index work.
- Decision/consequences: `retrieve` accepts optional department set, policy-ID set, and ISO-date `as_of` filters, applied before lexical ranking. With `as_of`, only active policies whose inclusive effective range contains that date are considered. Defaults preserve existing callers. Dates are compared lexically, relying on the documented ISO calendar-date contract; parsing/validation of arbitrary dates is not added here. This remains deterministic lexical retrieval, not vector search or persistent RAG.
- TDD: added a department-scoping behavior test and observed expected failure (`TypeError: retrieve() got an unexpected keyword argument 'departments'`), implemented the optional filter, then added effective-date/status behavior coverage.
- Files touched: `app/policies.py`, `tests/test_policy_retrieval.py`, `docs/ROADMAP.md`, this run record.
- Dependencies: none added or changed; used `.venv/Scripts/python.exe`.
- Tests/evaluation: `.venv/Scripts/python.exe -m pytest -q` — 6 passed; 13 existing LangSmith `asyncio.iscoroutinefunction` deprecation warnings. `git diff --check` clean. No retrieval quality metrics or persistent ingestion evaluation claimed.
- Security/artifact review: diff inspected; no credentials, network access, unsafe commands, generated artifacts, or new dependencies. No external MCP/service use.
- Commit SHA(s): pending.
- Remote HEAD: pending.
- Blocker: none for this slice; the broader RAG requirement remains unimplemented.
- Next step: implement controlled policy version/change simulation and persist version/status transitions in a catalog before incremental ingestion.
