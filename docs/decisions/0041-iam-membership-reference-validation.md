# Decision 0041 — Validate principal membership references

- Date: 2026-11-21. Goal: prevent principals from persisting memberships that refer to nonexistent or cross-tenant roles/groups.
- Context: P1a requires tenant-qualified membership references. An uncommitted partial change in `app/iam_store.py` was syntactically malformed; its accompanying tests specified valid role/group refs, rejection of missing refs, and transactional rollback.
- Decision: within the existing `BEGIN IMMEDIATE` principal write, require role and group IDs to exist under the membership tenant before storing the principal. Empty memberships remain supported. Memberships with no refs require the tenant to have a persisted role/group, matching the test contract. Failed validation rolls back before principal/audit/revision writes.
- Alternatives: validate only role/group refs when present (would allow orphan empty tenant memberships); defer validation to the authorization adapter (invalid state would persist); leave partial change untouched (fails collection and cannot be committed safely).
- Verification: initial focused test exposed the pre-existing SyntaxError; after repair focused suite passed (15). Full suite passed (86). Ruff `app tests`, compileall `app tests`, pip check, and `git diff --check` passed. 13 existing LangSmith deprecation warnings.
- Files committed: `app/iam_store.py`, `tests/test_iam_store.py`, this decision.
- Security/limits: SQLite local-only, no dependencies/network/customer data. This validates role/group references only; document/grant persistence and full revision-tagged authorization snapshot remain incomplete.
- Commit/remote: pending.
- Next: continue P1a with document/grant persistence and complete revision-tagged snapshot before P1b application enforcement.
