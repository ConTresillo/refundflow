# Decision 0032 — Explicit tenant-scoped role-permission links

- Date/run: 2026-11-18; scheduled IAM persistence slice on `dev`.
- Goal: close the next safe IAM catalog gap by persisting explicit permission assignment to a tenant-owned role.
- Context: tenant role and permission identifiers are already stored, but coexisting identifiers must never imply a grant. The PyCasbin adapter currently consumes immutable in-memory roles/principals/documents and is not wired to this SQLite store.
- Decision: add `iam_role_permissions` with a composite primary key and composite foreign keys to same-tenant roles and permissions. Add an audited, idempotent assignment API that validates both references and a tenant-scoped sorted read API. This is catalog persistence only; it does not add memberships, mutate policy evaluation or authorize documents.
- Alternatives: infer permissions from catalog contents (unsafe, rejected); directly wire store into Casbin (too broad and risks presenting incomplete snapshots as complete); a separate assignment table is explicit and auditable.
- Files: `app/iam_store.py`, `tests/test_iam_store.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none; reuse existing SQLite. No new library was needed for a relational integrity boundary.
- Tests/evaluation: `.venv/Scripts/python.exe -m pytest tests/test_iam_store.py -q`: 11 passed. `.venv/Scripts/python.exe -m pytest -q`: 81 passed (13 pre-existing LangSmith deprecation warnings). `.venv/Scripts/python.exe -m ruff check app/iam_store.py tests/test_iam_store.py`, `.venv/Scripts/python.exe -m compileall -q app/iam_store.py`, and `git diff --check` passed.
- Security/artifact review: assignment requires same-tenant existing role and permission; foreign keys enabled for connections; duplicate assignment emits no duplicate audit event. No retrieval/model integration, role assignment validation, document grants, or authorization refresh is claimed. Local-only synthetic data.
- Commit SHA: `f3cf7456cf0d19f4d7eb3bc5534b9eba4e3537b4`. Remote dev HEAD read back with `git ls-remote origin refs/heads/dev`: same SHA; worktree clean after push.
- Blocker: none for this persistence slice.
- Next: persist tenant-role assignments after validating the principal's tenant membership and same-tenant role, without implying document access.
