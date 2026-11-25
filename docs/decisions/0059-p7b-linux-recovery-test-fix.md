# Decision / run record 0059 — P7b recovery test made portable on Linux

- **Date:** 2026-11-25 (IST, UTC+05:30)
- **Run/work item:** RefundFlow scheduled builder — make the SQLite fail-closed
  recovery test suite work on every platform.
- **Goal:** Make `tests/test_observability_recovery.py` (P7b recovery suite)
  pass on Windows and Linux (Ubuntu 3.11/3.12/3.14) CI.
- **Context:** P7b recovery tests were delivered to `main` via PR #8 (squash
  merged, commit `44433ee`). The suite initially passed on Windows but failed
  on Ubuntu's Python 3.11/3.12/3.14 CI with:
  `FAILED tests/test_observability_recovery.py::TestDiskFailureInjection::
  test_append_event_fails_closed_when_database_file_is_readonly - sqlite3.
  OperationalError: unable to open database file`.
- **Root cause:** In `test_append_event_fails_closed_when_database_file_is
  _readonly`, the `finally` block restored only the write bit
  (`stat.S_IWRITE`). On Unix this leaves the file write-only (no read bits,
  mode `0o200`), so a subsequent `sqlite3.connect(db)` cannot open the
  database (`"unable to open database file"`), breaking the post-write
  "no partial row" verification. On Windows the same call clears the
  read-only flag correctly.
- **Decision:** Restore a fully readable/writable mode in the `finally`
  block, using `stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH`
  (mode `0o644`). This works on both platforms:
  - Unix: mode `0o644` includes read+write bits.
  - Windows: `os.chmod` maps the owner read+write bits to the readable+
    writable file attribute (clears the read-only flag).
- **Consequences:**
  - **Positive:** the recovery suite passes on Windows and all four CI
    matrix jobs (Python 3.11/3.12/3.14 Ubuntu + Windows).
  - **Negative:** none; no API or runtime change, pure test portability fix.
- **Touched files:**
  - `tests/test_observability_recovery.py` — fixed `finally` block chmod.
  - `docs/ROADMAP.md` — updated the "Current active slice (P7b)" section to
    reflect delivery on `main` and the verified full-suite result.
- **Tests / evaluation (actual output):**
  - `pytest tests/test_observability_recovery.py -q` → **8 passed**, EXIT=0.
  - Full suite: `pytest -q` → **219 passed**, EXIT=0.
  - `ruff check app tests` → **All checks passed.**
  - `compileall -q app tests` → clean.
  - `pip check` → **No broken requirements found.**
  - CI on `feat/p7b-recovery-to-main` (PR #8 squash merge): 4/4 jobs
    **SUCCESS** (Python 3.11/3.12/3.14 Ubuntu + Windows).
- **Commit / remote state:**
  - Commits on `feat/p7b-recovery-to-main`: `87f0a33` (recovert suite),
    `103c524` (Linux fix).
  - PR #8 squash-merged to `main`: `44433ee` (merge commit).
  - Remote `main` SHA: `44433ee` (verified via `git ls-remote`).
- **Blocker / next step:** No blocker. Remaining open work is the broader
  roadmap (async evidence queue, semantic cache, evaluation matrix).
