# Decision 0019 — sync outcome survives failed transaction

- Date/run: 2026-11-17; scheduled RefundFlow development run.
- Goal: close a narrowly testable failure invariant from Decision 0018 while generation history remains a separate slice.
- Context: latest per-policy sync outcome and catalog/FTS writes share one SQLite transaction. Prior tests verified catalog and FTS rollback, but not that a failed attempted sync leaves the durable previous outcome visible.
- Decision: add a regression proving an injected failure after attempted catalog/index mutations leaves the last successful outcome, catalog version, and indexed evidence unchanged. No production behavior or schema changes are needed; SQLite rollback already satisfies the contract.
- Alternatives: implement run-generation persistence and failed-run recording in the same change (rejected: larger contract/schema slice); alter transaction code without evidence of a defect (rejected: test directly verifies existing behavior).
- Tests/evaluation: RED: `.venv/Scripts/python.exe -m pytest tests/test_policy_ingestion.py::test_failed_sync_preserves_durable_successful_outcome -q` failed on the outcome assertion (`changed` was observed rather than the expected previous `added`). GREEN: focused test passed. Full `.venv/Scripts/python.exe -m pytest -q` — 34 passed; 13 existing LangSmith asyncio deprecation warnings. `.venv/Scripts/python.exe -m ruff check app tests` passed; `.venv/Scripts/python.exe -m compileall -q app tests` passed; `.venv/Scripts/python.exe -m pip check` reported no broken requirements; `git diff --check` passed.
- Files: `tests/test_policy_ingestion.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none.
- Security/artifact review: local SQLite fixture only; no credentials, network, generated artifacts or external state.
- Commit/push/CI: pending.
- Blocker/limitations: this is an invariant regression test, not immutable generation/manifest support; failed attempts are still not recorded as durable runs. Rollback/rebuild remain unimplemented and Chroma remains gated.
- Next: add append-only sync run/item records and complete manifests with an atomic active-generation pointer, retaining prior active state on transactional failure. Then implement rollback/rebuild separately.
