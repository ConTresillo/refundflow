# Run record 0009 — incremental SQLite FTS5 index and Actions workflows

- Date/run: 2026-11-16; scheduled invocation (scheduler run ID unavailable).
- Goal: complete the in-progress standalone incremental local index slice and establish secure CI plus manual source-artifact delivery.
- Baseline: `f6d75b7` (`main` matched `origin/main`). Existing staged/unstaged changes were inspected and preserved; no earlier run record described this unfinished slice. GitHub Actions had no runs (`gh run list` returned `[]`).
- Decision: keep the SQLite FTS5 index separate from application retrieval until a traceable adapter/freshness contract is tested; accept full-version snapshots, explicit empty scopes, rollback, section-reusable chunks, immutable policy-version department ownership. Add CI via reusable workflow and manual main-only source bundle rather than external publishing/deployment.
- Alternatives: wire into retrieval now without agreed citation/effective-date adapter contract; infer deletion from absent input; use unordered commit-delivery zip; deploy externally. Rejected for freshness/safety/contract and approval reasons.
- Files: `.github/workflows/ci.yml`, `.github/workflows/deliver-source.yml`, `app/incremental_index.py`, `tests/test_incremental_index.py`, `docs/ROADMAP.md`, `docs/architecture/LLD.md`, `docs/architecture/README.md`, `docs/architecture/ci-cd.md`, this record.
- Dependencies: none. Existing `.venv/Scripts/python.exe`, Python 3.14.9 / locked environment.
- TDD: baseline staged tests passed (9). Added red tests for mixed/changed department ownership and multiple chunks per section; observed two expected assertion failures (missing validation and a uniqueness collision). Implemented input validation and removed inappropriate section/hash uniqueness constraint; both regression tests then passed.
- Tests: `.venv/Scripts/python.exe -m pytest tests/test_incremental_index.py -q` → 9 passed. Full `.venv/Scripts/python.exe -m pytest -q` → 20 passed, 13 pre-existing LangSmith deprecation warnings. `compileall -q app tests`, `pip check` (No broken requirements), and `git diff --check` passed.
- Actions: no run exists for the in-progress files because they were not yet committed/pushed. Workflow execution on pushed SHA remains required; do not claim green.
- Security/artifact review: GitHub Actions use contents:read, no secrets, SHA-pinned actions, bounded job timeouts and PR concurrency cancellation; source artifact only on manual main dispatch, 14-day retention, commit-addressed tar and checksum. Added-line scan found no hardcoded credentials, shell/eval/exec/pickle/SQL interpolation patterns. No external services, deployment, or package publication. Workflow files reviewed against current official GitHub Actions workflow syntax, reusable-workflow, and secure-use documentation.
- Commit SHA: pending verification.
- Remote HEAD: pending push/read-back verification.
- Blocker: none for commit/push; future end-to-end retrieval requires an application adapter enforcing policy status/effective dates and emitting citations. CI result pending push.
- Next: wire SQLite FTS5 into policy retrieval with effective-date/status freshness and citation/evaluation tests, then run/repair CI matrix and verify the manual source-delivery workflow behavior without publishing/deploying externally.
