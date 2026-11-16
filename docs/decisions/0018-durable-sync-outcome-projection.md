# Decision 0018 — durable sync outcome projection

- Date/run: 2026-11-17; scheduled RefundFlow development run.
- Goal: make the last successful per-policy ingestion outcome durable across service restarts, while retaining the scope boundary of Decision 0017.
- Context: Decision 0017 keeps outcome state in memory, while catalog provenance is SQLite-backed. Tests establish current provenance and transaction rollback behavior; no generation/run history is implemented. Chroma remains gated.
- Decision: persist one latest outcome row per policy in the catalog SQLite database, recording outcome and sync timestamp in the same transaction as catalog and FTS changes. Status joins the current catalog record with this read model; it remains interim and does not claim immutable run history, manifests, removed-policy visibility, or recovery.
- Alternatives: retain memory-only outcomes (rejected: restart loses report); add full generation schema now (deferred: broader slice); write outcomes after catalog commit in a separate transaction (rejected: failure can leave status inconsistent with the sync).
- Invariants: catalog and index continue sharing one SQLite database; outcomes commit atomically with lifecycle and FTS updates; failed sync preserves prior persisted outcomes; schema is self-initializing; local SQLite only, no Chroma or external effects.
- Tests: RED observed for the new service-recreation test (`active` returned instead of expected `added`). GREEN: `.venv/Scripts/python.exe -m pytest tests/test_policy_ingestion.py -q` — 9 passed; full suite — 33 passed (13 existing LangSmith asyncio deprecation warnings); focused Ruff, `compileall -q app tests`, `pip check`, and `git diff --check` passed.
- Files: `app/policy_catalog.py`, `app/policy_ingestion.py`, `tests/test_policy_ingestion.py`, `docs/ROADMAP.md`, this record.
- Dependencies: none.
- Security/artifact review: no credentials, network calls, dependencies, or generated artifacts; no external effects.
- Commit/push/CI: pending.
- Blocker/limitations: latest projection only (overwrites prior outcomes); no Decision 0016 generation history/manifest/pointer, rollback/rebuild, persisted failed-run status, or removed-document status.
- Next: implement durable successful/failed ingestion generations and immutable manifests with transaction failure proving active snapshot stability; recovery follows separately. Chroma remains gated.
