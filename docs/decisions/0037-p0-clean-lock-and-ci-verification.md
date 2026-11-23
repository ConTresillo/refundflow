# Decision 0037 — Verify clean locked environment and exact-SHA CI

- Date: 2026-11-20. Goal: close P0a/P0b reproducibility evidence without changing dependency or product code.
- Context: decision 0036 repaired Python 3.10 test collection; local pre-populated environment was insufficient evidence for P0a.
- Decision: build a disposable Windows/Python 3.14 venv on E: from `requirements-dev.lock`, install project editable with `--no-deps`, then test imports and project gates. The existing locked closure is adequate; no dependency change.
- Verification: `python -m pip install -r E:/RefundFlow/requirements-dev.lock` and `python -m pip install --no-deps -e E:/RefundFlow` succeeded in `.scratch/p0a-clean/venv`; `pip check` reported no broken requirements; `import casbin` and `CasbinAuthorizationAdapter` import passed. Full pytest: 82 passed, 13 existing LangSmith deprecation warnings. Ruff (`app tests`), compileall (`app tests`), pip check and `git diff --check` passed. Disposable environment removed afterward.
- CI: `gh run view 44194127163 --json headSha,conclusion,jobs` reports success at exact SHA `9b339cf9206620bbf404603234b096c395ee198f`; all four matrix jobs (Windows 3.14, Ubuntu 3.10/3.12/3.14) passed.
- Security/artifact review: no application/dependency changes, external services or customer data. Temporary venv and downloaded packages were confined to project `.scratch` and removed. Pre-existing unrelated documentation changes were preserved and excluded.
- Files: `docs/ROADMAP.md`, this record.
- Commit / remote HEAD: pending.
- Blocker / next: P0 verification complete. Proceed with P1a tenant-scoped assignments and atomic snapshot/revocation coverage; P1b UI wiring follows.