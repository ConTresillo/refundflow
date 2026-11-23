# Decision 0042 — Persist tenant-scoped documents and grants

- Date: 2026-11-21. Goal: add one P1a IAM persistence slice for explicit document/grant state.
- Context: SQLiteIAMStore held principals and role graph but had no durable document authorization resources. Document/DocumentGrant models and PyCasbin adapter already exist. P1a requires the complete revisioned snapshot before web wiring.
- Alternatives: infer grants from role/permission coexistence (implicit unsafe access); use isolated in-memory models (not durable); add a new evaluator (duplicates selected PyCasbin).
- Decision: store tenant-qualified document records plus explicit grant rows in SQLite. Mutating a document replaces its grant set, appends a redacted-count audit event and advances the authorization revision atomically. Identical writes are idempotent. Snapshot returns documents and current revision from one read transaction.
- Tests: regression first failed with AttributeError (missing put_document). `pytest tests/test_iam_store.py tests/test_iam.py -q`: 28 passed. Full suite: 88 passed. Ruff app/tests, compileall, pip check and diff check passed; 13 existing LangSmith deprecation warnings.
- Security/limits: synthetic local records only, no dependency/network. Document JSON is canonical app storage and grants are duplicated relationally for integrity/queryability; snapshot integrity tests pass. Full all-entities single snapshot, tenant-qualified role evaluator IDs, references/foreign keys on every mutation, deletion/revocation API, and application enforcement remain incomplete. Local audit is not tamper-proof.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, this record and ROADMAP.
- Commit/remote: pending.
- Next: finish P1a with one consistent full authorization snapshot including principals, tenant roles/permissions/assignments, documents/grants and revision; validate tenant-qualified Casbin role edges before P1b wiring.
