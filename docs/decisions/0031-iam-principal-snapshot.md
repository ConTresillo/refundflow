# Decision 0031 — Read-only principal snapshot for authorization refresh

- Date/run: 2026-11-17; scheduled IAM persistence slice on `dev`.
- Goal: establish a deterministic store boundary for reading persisted IAM principals as input to a future Casbin snapshot refresh, without converting catalog identifiers into access grants.
- Context: Decision 0030 persisted tenant-scoped permission identifiers; roles are also stored by tenant. Neither role-to-permission assignments nor document grants have a persisted representation. Inferring relationships by coexisting IDs would grant unintended access.
- Decision: add `SQLiteIAMStore.authorization_snapshot()`, a deterministic read of persisted principals only. It returns no roles or permissions. It does not authorize requests, perform a Casbin refresh, or claim complete IAM snapshot support. This conservative result preserves deny-by-default until explicit role/permission assignment persistence exists.
- Alternatives: infer assignments from tenant catalogs (rejected: unsafe privilege expansion); expose a misleading partial role snapshot (rejected); defer all refresh plumbing (rejected: a read-only principal boundary is useful and safe).
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none; existing SQLite and PyCasbin remain unchanged.
- Tests/evaluation: baseline `.venv/Scripts/python.exe -m pytest tests/test_iam.py tests/test_iam_store.py -q`: 18 passed. Added two behavior tests, observed RED (`AttributeError`, missing snapshot method), then implemented. Final focused `.venv/Scripts/python.exe -m pytest tests/test_iam_store.py tests/test_iam.py -q`: 20 passed (13 pre-existing LangSmith deprecation warnings). Full `.venv/Scripts/python.exe -m pytest -q`: 79 passed (same 13 warnings). `.venv/Scripts/python.exe -m ruff check app/iam_store.py tests/test_iam_store.py`, `.venv/Scripts/python.exe -m compileall -q app/iam_store.py`, and `git diff --check` passed.
- Security/artifact review: only existing principal membership JSON is decoded through validated `Membership`/`Principal` models; no role/permission relationship is inferred. Tenant trust, role assignments, document grants, versioned Casbin refresh, retrieval authorization and revocation rechecks remain unimplemented. No external service/customer data used.
- Commit SHA: pending. Remote dev HEAD: pending.
- Blocker: none for this bounded safe read slice.
- Next: persist explicit tenant-role assignments and resolve role permissions only through explicit, tenant-owned links; deny incomplete assignments.
