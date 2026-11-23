# Decision 0034 — Repair PyCasbin Windows lock closure

- Date: 2026-11-19. Status: local dependency-lock repair implemented; clean-install and exact-SHA CI acceptance remain open.
- Goal: resolve the known Windows/Python 3.14 CI `pip check` failure at baseline `dfaea230618d98de8a12de610df90a1436abc910` without changing runtime metadata or weakening CI.
- Evidence: inspected installed distributions with `pip show` and `pip inspect` on the project Windows Python 3.14.9 venv. PyCasbin `2.8.0` requires `simpleeval>=1.0.3` and `wcmatch>=10.1`; wcmatch requires `bracex>=3.2.3`. These transitive distributions were already pinned in `requirements-dev.lock`; only direct `pycasbin==2.8.0` was absent. No packages installed.
- Decision: pin the already-selected PyCasbin version in the Windows lock and add `tests/test_lock_consistency.py`, which validates normalized active runtime distribution names are represented in the lock. No other lock pins or project dependency metadata changed.
- Files: `requirements-dev.lock`, `tests/test_lock_consistency.py`, `docs/ROADMAP.md`, this record.
- Verification on current venv: focused lock test passed; full `pytest -o addopts='' -q`: 82 passed, 13 existing LangSmith deprecation warnings; `ruff check app tests`: passed; `compileall -q app tests`: passed; `pip check`: passed; `git diff --check`: passed. One intermediate Ruff run found an import-order issue in the new test; it was auto-fixed and lint rerun successfully.
- Not verified: isolated clean Windows lock installation, `--no-deps -e .` install in that environment, import regression there, or four-job CI at pushed SHA. Thus P0a/P0b are not complete.
- Security/dependency review: existing PyCasbin dependency only; lock addition exact and grounded in installed version. No network/package install, secrets, external services or customer data used. No commit/remote SHA yet at record creation.
- Next: create isolated E:-workspace/approved scratch Python 3.14 environment, install the Windows lock, editable project with `--no-deps`, prove `pip check`, evaluator import and CI-equivalent checks; then push dev and inspect all four jobs at exact SHA.
