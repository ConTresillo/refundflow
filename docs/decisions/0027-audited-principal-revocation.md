# Decision 0027 — Audited principal deletion and membership revocation

- Date/run: 2026-11-17; scheduled IAM persistence slice on `dev`.
- Goal: close a concrete Phase 2 gap: revoking a principal from authoritative local membership state with an audit record.
- Context: Decision 0026 persisted and audited membership replacement but had no deletion operation. Retaining a principal with empty memberships can revoke tenant access, but does not model lifecycle deletion; unaudited direct SQL would violate the store boundary.
- Decision: add `SQLiteIAMStore.delete_principal`, returning whether a row existed. For an existing principal, deletion and a `principal.deleted` event commit atomically. An absent principal returns false without adding a misleading duplicate event. The timestamp is explicitly caller-supplied/defaulted like other store mutations.
- Alternatives: soft-disable field requires a domain lifecycle decision and schema migration; direct SQL is not auditable; reusing empty-membership update retains the principal. This is a small hard-delete operation, not an account lifecycle service.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Verification: focused IAM store/domain tests: 14 passed (13 pre-existing LangSmith deprecation warnings). Full `.venv/Scripts/python.exe -m pytest -q`: 73 passed. `.venv/Scripts/python.exe -m ruff check app tests`: passed; Ruff format check was initially red on the new test and passed after formatting. Compileall and `git diff --check` passed. No API exists for this store, so no server smoke applies.
- Security/artifact review: local-only SQLite, no credentials or customer data; transaction protects deletion/audit consistency. Hard deletion has no recovery facility and is not wired into identity or retrieval authorization; callers must rebuild their authorization snapshot after mutation.
- Commit/remote: pending.
- Blocker/next: none for this slice. Next: persist remaining IAM entities and audited grants, then define and test snapshot refresh/revocation integration before retrieval. Remote SHA must be read back after push.
