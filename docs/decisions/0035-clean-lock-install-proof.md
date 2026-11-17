# Decision 0035 — Clean lock-install proof (Python 3.11 host substitute)

- Date: 2026-11-19. Goal: advance P0a by testing the Windows lock in an isolated environment.
- Context: project venv is Python 3.14.9, but host `py -0p` lists only Python 3.11.9 and 3.10; no 3.14 interpreter is registered. P0a explicitly requires clean Python 3.14, so this is partial evidence only.
- Decision: create an isolated venv at `E:/refundflow-p0a-venv` with Python 3.11.9, install the exact `requirements-dev.lock`, install the project editable with `--no-deps`, then run the CI-equivalent local checks. No dependency versions or lock content changed.
- Outcome: lock installation succeeded; editable install succeeded; `pip check` reported no broken requirements; `import casbin` and `from app.iam import CasbinAuthorizationAdapter` succeeded; `pytest -o addopts='' -q` passed (82); Ruff, compileall, and `git diff --check` passed.
- Limits/blocker: not the required Python 3.14 clean install. P0a remains open. Exact-SHA four-job CI inspection (P0b) remains pending. Next unblock: run this isolated procedure on a host/runner with Python 3.14, then push verified `dev` work and inspect all four jobs at that SHA.
- Security/artifact review: only locked packages installed into an isolated E: venv; no customer data, credentials, external service, or public exposure. Existing user documentation changes were preserved and not included in this run's edits.
- Files changed: `docs/ROADMAP.md`, this decision record. No dependency changes.
- Commit / remote HEAD: pending at record creation.
