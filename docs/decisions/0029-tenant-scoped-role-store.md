# Decision 0029 — Persist tenant-scoped roles

- Date/run: 2026-11-17; scheduled IAM persistence slice on `dev`.
- Goal: complete one small remaining IAM entity persistence item.
- Context: `SQLiteIAMStore` already persisted principals and tenant-scoped groups with atomic audit events. Phase 2 calls for tenant/user/group/membership/role/permission state. No role entities could yet be enumerated or durably scoped.
- Decision: add a tenant/role composite-key SQLite table, idempotent `put_role`, ordered tenant-filtered `list_roles`, and transactionally append `role.created` only on insertion. Validate all identifiers/timestamp as non-empty. Keep storage local and explicit; no implicit authorization effect.
- Alternatives: role identifiers embedded in principal JSON do not model tenant-owned entities; global role IDs can collide across tenants; adopting an external authorization service is unnecessary for this persistence increment.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, `docs/decisions/0028-tenant-scoped-group-store.md`, this record.
- Dependencies: none.
- Verification: baseline full suite before edits: 75 passed. After edits, focused store tests: 6 passed; full `.venv/Scripts/python.exe -m pytest -q`: 76 passed (13 LangSmith deprecation warnings). `.venv/Scripts/python.exe -m ruff check app tests`, formatter check on changed Python files, compileall and `git diff --check` passed.
- Security/limits: tenant key is caller-supplied, not trusted identity; role persistence alone grants no permissions. Role deletion, permissions, grants, identity enforcement, and Casbin snapshot refresh remain unimplemented. Local SQLite only; no external services or customer data.
- Commit/remote: pending.
- Next: define and test the path from authoritative persisted role/permission/grant mutations to refreshed authorization snapshot, including fail-closed revocation before retrieval.
