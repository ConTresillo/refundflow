# Decision 0036 — Restore Python 3.10 CI test collection

- Date: 2026-11-20. Goal: repair the remaining matrix failure identified during P0b exact-SHA review.
- Context: run `44183686766` at `5b8a203bd8b942dffea1f035978a13f0b5bfaf85` failed overall. Windows/Python 3.14 and Ubuntu/Python 3.12 and 3.14 passed. Ubuntu/Python 3.10 failed collecting `tests/test_lock_consistency.py`: `ModuleNotFoundError: No module named 'tomllib'`. `tomli` is already a project/test dependency; no new dependency is needed.
- Decision: use stdlib `tomllib` where available and fall back to existing `tomli` on Python 3.10. Keep the lock-consistency regression enabled.
- Verification: focused test passed (1); full suite passed (82); Ruff, compileall, pip check and diff check passed in the Python 3.14 project environment. Thirteen existing LangSmith deprecation warnings were emitted. No Python 3.10 local runtime is available. The prior remote CI SHA remains failed; inspect all four jobs again at the pushed fix SHA.
- Security/artifact review: test-only compatibility import, no runtime behavior or dependency changes, no external data/services or credentials. Preserved pre-existing user documentation modifications; only this record, roadmap evidence and the focused test are intended for this commit.
- Files: `tests/test_lock_consistency.py`, `docs/ROADMAP.md`, this record.
- Commit / remote HEAD: pending.
- Blocker / next: exact-SHA CI confirmation pending. Continue P0a clean Python 3.14 lock-install proof after matrix repair confirmation.
