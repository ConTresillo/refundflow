# Decision 0030 — Persist tenant-scoped permission identifiers

- Date/run: 2026-11-17; scheduled IAM persistence slice on `dev`.
- Goal: persist permission catalog identifiers as the next bounded IAM entity after tenant-scoped groups and roles.
- Context: Phase 2 requires permissions in the enterprise IAM model. `SQLiteIAMStore` persisted tenant-scoped groups and roles, but no permission catalog. Existing `app/iam.py` models validated permission names and Casbin authorization; persistence must not silently imply grants.
- Decision: add tenant/permission composite-key SQLite storage, idempotent `put_permission`, deterministic tenant-filtered `list_permissions`, and transactionally append `permission.created` only for a new row. Require non-empty tenant, permission, and timestamp. Keep permissions distinct from role assignments and resource grants.
- Alternatives: placing permission IDs in an unscoped global table risks tenant collisions; storing them in role policy would conflate catalog entities with access grants. No new authorization dependency is needed for this persistence slice.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none.
- Tests/evaluation: Before implementation, added a behavior test and ran `.venv/Scripts/python.exe -m pytest tests/test_iam_store.py -q`: expected failure (`AttributeError`, missing `put_permission`). After implementation, focused store tests: 7 passed. Full `.venv/Scripts/python.exe -m pytest -q`: 77 passed, 13 pre-existing LangSmith deprecation warnings. `.venv/Scripts/python.exe -m ruff check app/iam_store.py tests/test_iam_store.py`, `.venv/Scripts/python.exe -m compileall -q app/iam_store.py`, and `git diff --check` passed.
- Security/artifact review: Values are parameterized SQL and tenant-filtered reads. Tenant identifiers remain caller-supplied and are not identity proof. Permission catalog rows do not grant access; role assignment, grants, identity trust, Casbin snapshot refresh and retrieval gating remain open. No customer data, external services, or secrets used.
- Commit SHA: pending. Remote dev HEAD: pending.
- Blocker: none for this slice.
- Next: connect persisted IAM data to explicit versioned Casbin refresh; ensure revocation fails closed before retrieval.
