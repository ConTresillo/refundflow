# Decision 0040 — Revision covers effective role-graph writes

- Date: 2026-11-21. Goal: ensure revision-tagged authorization consumers can detect role/permission mutations, not only membership changes.
- Context: the existing authorization revision advanced for principal replace/delete, but tenant-scoped `put_role`, `put_permission`, and role-permission assignment changed authorization input without advancing it. A consumer could retain an enforcer at the same revision while role graph data changed.
- Decision: increment the existing SQLite revision in the same transaction as each effective role, permission, or role-permission insert and its audit event. Idempotent duplicate insert remains a no-op. Centralize the increment helper and use it for principal deletion too. No change to the revision snapshot contents or claim of complete IAM authorization.
- Alternatives: infer revisions from audit sequence (less explicit contract); increment on every call (needless invalidations for no-op writes); leave these writes outside the revision (stale-policy risk).
- Test-first evidence: added a behavior test for role creation, permission creation, assignment, and duplicate idempotency; it failed before implementation because role creation left revision at zero.
- Verification: `.venv/Scripts/python.exe -m pytest tests/test_iam_store.py -q` — 14 passed. `.venv/Scripts/python.exe -m pytest -q` — 85 passed (13 existing LangSmith deprecation warnings). Ruff, compileall, pip check, and `git diff --check` passed.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Security/limits: local SQLite only; no dependencies, network, or customer data. Tenant role identities, documents/grants and their snapshot/revocation behavior remain incomplete. Does not complete P1a.
- Commit/remote: pending.
- Next: continue P1a with remaining validated entities and complete revision-tagged snapshot; then P1b application enforcement.
