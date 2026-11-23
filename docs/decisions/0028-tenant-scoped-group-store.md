# Decision 0028 — Persist tenant-scoped groups

- Date/run: 2026-11-17; scheduled IAM persistence slice on `dev`.
- Goal: implement the first remaining IAM entity in the current Phase 2 slice.
- Context: `SQLiteIAMStore` held memberships but group IDs existed only inside those memberships; the roadmap explicitly calls for tenant/group persistence. Casbin remains the policy evaluator; this change is limited to authoritative local entity storage.
- Decision: add a SQLite group table keyed by `(tenant_id, group_id)`, idempotent `put_group`, tenant-filtered ordered `list_groups`, and an audit event for each newly created group. Duplicate creation is a no-op and does not duplicate audit entries. Reject empty identifiers.
- Alternatives: global group IDs risk cross-tenant collisions; external OpenFGA deployment is disproportionate for this embedded local persistence slice; storing groups only in principal JSON cannot enumerate or independently scope groups.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Verification: focused store tests: 5 passed. Full `.venv/Scripts/python.exe -m pytest -q`: 75 passed (13 pre-existing LangSmith deprecation warnings). `.venv/Scripts/python.exe -m ruff check app tests`: passed; format check passed for changed Python files after formatting; compileall and `git diff --check` passed.
- Security/limits: local SQLite only; tenant ID is an explicit method argument, not trusted identity. No user/group membership relation mutation, role/grant storage, API exposure, or retrieval authorization is added. Group creation is currently append-only; deletion lifecycle remains unmodeled.
- Dependency changes: none.
- Commit/remote: pending.
- Follow-up: a subsequent small slice added tenant-scoped role persistence in `app/iam_store.py` with idempotent creation, ordered tenant-only listing and atomic `role.created` audit. Tests cover duplicate behavior, same role ID in separate tenants, empty input, and audit details.
- Blocker/next: permission/document-grant state and tested snapshot refresh/revocation integration before retrieval.
