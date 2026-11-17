"""Controlled, review-before-publish policy changes for the local workspace."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.incremental_index import SQLitePolicyIndex
from app.policies import compute_content_hash, load_registry
from app.policy_catalog import PolicyCatalog
from app.policy_ingestion import PolicyIngestionService, PolicySyncResult
from app.retrieval import SQLiteEvidenceRetriever


class PolicyChangeError(ValueError):
    """A policy draft is invalid, stale, or not in a publishable state."""


class PolicyChangeService:
    """Persist reviewable drafts and atomically publish them through SQLite RAG.

    The workspace JSON is the editable source snapshot. SQLite catalog, FTS rows,
    successful publication report, and draft state change in one transaction.
    Failed writes restore the prior source bytes when the file has not been
    concurrently modified.
    """

    _POLICY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
    _VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
    _EDITABLE_FIELDS = ("title", "effective_from", "effective_to", "excerpts")

    def __init__(
        self,
        source_dir: str | Path,
        catalog: PolicyCatalog,
        index: SQLitePolicyIndex,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source_dir = Path(source_dir).resolve()
        self._catalog = catalog
        self._index = index
        self._ingestion = PolicyIngestionService(self._source_dir, catalog, index)
        self._retriever = SQLiteEvidenceRetriever(index, catalog)
        self._database = Path(catalog.database).resolve()
        if self._database != Path(index.path).resolve():
            raise ValueError(
                "policy change service requires one shared SQLite database"
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS policy_change_drafts (
                    draft_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    base_version TEXT NOT NULL,
                    base_hash TEXT NOT NULL,
                    new_version TEXT NOT NULL,
                    new_hash TEXT NOT NULL,
                    base_source_bytes BLOB NOT NULL,
                    proposed_record_json TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    diff_text TEXT NOT NULL,
                    diff_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('draft', 'reviewed', 'published')),
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    published_at TEXT,
                    published_run_id TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_policy_change_drafts_state
                    ON policy_change_drafts(state, created_at);
                CREATE TABLE IF NOT EXISTS policy_change_runs (
                    run_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES policy_change_drafts(draft_id),
                    policy_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
                    base_version TEXT NOT NULL,
                    new_version TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    source_restored INTEGER,
                    error_code TEXT,
                    report_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_policy_change_runs_history
                    ON policy_change_runs(completed_at DESC, run_id);
                """
            )

    def initialize_empty_database(self) -> bool:
        """Index the seeded workspace once; never auto-sync an existing catalog."""
        if self._catalog.current_records():
            return False
        sources = load_registry(self._source_dir)
        if not sources:
            raise PolicyChangeError("policy workspace has no source documents")
        self._ingestion.sync(occurred_at=self._now_iso())
        return True

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _now_iso(self) -> str:
        return self._now().isoformat(timespec="seconds").replace("+00:00", "Z")

    def _source_path(self, policy_id: str) -> Path:
        if not self._POLICY_ID.fullmatch(policy_id):
            raise PolicyChangeError("invalid policy ID")
        path = (self._source_dir / f"{policy_id}.json").resolve()
        if path.parent != self._source_dir:
            raise PolicyChangeError("policy source path escapes the workspace")
        return path

    @staticmethod
    def _json(record: dict[str, Any]) -> str:
        return json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _record_without_hash(record: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in record.items() if key != "content_hash"}

    @staticmethod
    def _format_diff(old_record: dict[str, Any], new_record: dict[str, Any]) -> str:
        lines = [
            f"Policy: {old_record['id']}",
            f"Version: {old_record['version']} -> {new_record['version']}",
        ]
        old_hash = old_record.get("content_hash") or compute_content_hash(old_record)
        new_hash = new_record.get("content_hash") or compute_content_hash(new_record)
        if old_hash != new_hash:
            lines.append(f"Content hash: {old_hash} -> {new_hash}")
        for field in ("title", "effective_from", "effective_to"):
            if old_record.get(field) != new_record.get(field):
                lines.append(
                    f"{field}: {old_record.get(field)!r} -> {new_record.get(field)!r}"
                )
        lines.extend(
            difflib.unified_diff(
                old_record.get("excerpts", []),
                new_record.get("excerpts", []),
                fromfile=f"{old_record['id']}@{old_record['version']}",
                tofile=f"{new_record['id']}@{new_record['version']}",
                lineterm="",
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def create_draft(
        self,
        *,
        policy_id: str,
        expected_version: str,
        expected_hash: str,
        new_version: str,
        title: str,
        effective_from: str,
        effective_to: str | None,
        excerpts: list[str],
    ) -> dict[str, Any]:
        """Create a durable proposal; this never changes source, catalog, or FTS."""
        source_path = self._source_path(policy_id)
        if not self._VERSION.fullmatch(new_version):
            raise PolicyChangeError(
                "new version must be a short identifier without spaces"
            )
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise PolicyChangeError("title must contain 1 to 200 characters")
        if not isinstance(excerpts, list) or not 1 <= len(excerpts) <= 20:
            raise PolicyChangeError("provide between 1 and 20 policy clauses")
        if any(
            not isinstance(item, str) or not item.strip() or len(item) > 5000
            for item in excerpts
        ):
            raise PolicyChangeError(
                "each policy clause must contain 1 to 5000 characters"
            )
        if sum(len(item) for item in excerpts) > 20000:
            raise PolicyChangeError("combined policy text exceeds 20000 characters")
        try:
            start = date.fromisoformat(effective_from)
            end = date.fromisoformat(effective_to) if effective_to else None
        except (TypeError, ValueError) as error:
            raise PolicyChangeError(
                "effective dates must be ISO calendar dates"
            ) from error
        if end is not None and end < start:
            raise PolicyChangeError("effective_to cannot precede effective_from")
        if start > self._now().date():
            raise PolicyChangeError(
                "future effective dates require a scheduled-publish workflow"
            )

        current = self._catalog.current(policy_id)
        if current is None or current.get("status") != "active":
            raise PolicyChangeError("only an active catalog policy can be edited")
        if (
            current["version"] != expected_version
            or current["content_hash"] != expected_hash
        ):
            raise PolicyChangeError(
                "policy changed since the editor loaded it; refresh and retry"
            )
        if self._catalog.version(policy_id, new_version) is not None:
            raise PolicyChangeError(
                "policy versions are immutable; choose a new unused version"
            )
        try:
            base_source_bytes = source_path.read_bytes()
            source = load_registry(self._source_dir).get(policy_id)
        except (OSError, ValueError) as error:
            raise PolicyChangeError(
                "policy source is unavailable or invalid"
            ) from error
        if (
            source is None
            or source.get("version") != current["version"]
            or source.get("content_hash") != current["content_hash"]
        ):
            raise PolicyChangeError(
                "policy source differs from the active catalog; review source drift first"
            )

        proposed = self._record_without_hash(source)
        proposed.update(
            {
                "version": new_version,
                "title": title.strip(),
                "effective_from": start.isoformat(),
                "effective_to": end.isoformat() if end else None,
                "status": "active",
                "excerpts": [item.strip() for item in excerpts],
            }
        )
        changed_fields = [
            field
            for field in self._EDITABLE_FIELDS
            if proposed.get(field) != source.get(field)
        ]
        if not changed_fields:
            raise PolicyChangeError(
                "draft has no document changes beyond its version label"
            )
        new_hash = compute_content_hash(proposed)
        diff_text = self._format_diff(source, proposed)
        diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
        draft_id = uuid.uuid4().hex
        created_at = self._now_iso()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO policy_change_drafts
                (draft_id, policy_id, base_version, base_hash, new_version, new_hash,
                 base_source_bytes, proposed_record_json, changed_fields_json, diff_text,
                 diff_hash, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)""",
                (
                    draft_id,
                    policy_id,
                    current["version"],
                    current["content_hash"],
                    new_version,
                    new_hash,
                    base_source_bytes,
                    self._json(proposed),
                    self._json({"fields": changed_fields}),
                    diff_text,
                    diff_hash,
                    created_at,
                ),
            )
        return self.get_draft(draft_id)

    def _draft_row(self, draft_id: str) -> sqlite3.Row:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM policy_change_drafts WHERE draft_id=?", (draft_id,)
            ).fetchone()
        if row is None:
            raise PolicyChangeError("policy draft not found")
        return row

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        row = self._draft_row(draft_id)
        return {
            "draft_id": row["draft_id"],
            "policy_id": row["policy_id"],
            "base_version": row["base_version"],
            "base_hash": row["base_hash"],
            "new_version": row["new_version"],
            "new_hash": row["new_hash"],
            "proposed_record": json.loads(row["proposed_record_json"]),
            "changed_fields": json.loads(row["changed_fields_json"])["fields"],
            "diff": row["diff_text"],
            "diff_hash": row["diff_hash"],
            "state": row["state"],
            "created_at": row["created_at"],
            "reviewed_at": row["reviewed_at"],
            "published_at": row["published_at"],
            "published_run_id": row["published_run_id"],
        }

    def review_draft(self, draft_id: str, *, diff_hash: str) -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT state, diff_hash FROM policy_change_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
            if row is None:
                raise PolicyChangeError("policy draft not found")
            if row["diff_hash"] != diff_hash:
                raise PolicyChangeError(
                    "review acknowledgement does not match the exact diff"
                )
            if row["state"] == "draft":
                connection.execute(
                    "UPDATE policy_change_drafts SET state='reviewed', reviewed_at=? WHERE draft_id=? AND state='draft'",
                    (self._now_iso(), draft_id),
                )
            elif row["state"] != "reviewed":
                raise PolicyChangeError("only an unpublished draft can be reviewed")
        return self.get_draft(draft_id)

    def _run(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT report_json FROM policy_change_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise PolicyChangeError("publication report not found")
        return json.loads(row["report_json"])

    def publish_draft(self, draft_id: str) -> dict[str, Any]:
        """Publish one reviewed draft and verify catalog, chunk, and FTS snapshots."""
        row = self._draft_row(draft_id)
        if row["state"] == "published":
            return self._run(row["published_run_id"])
        if row["state"] != "reviewed":
            raise PolicyChangeError("draft must be reviewed before publication")

        policy_id = row["policy_id"]
        source_path = self._source_path(policy_id)
        proposed = json.loads(row["proposed_record_json"])
        proposal_bytes = (self._json(proposed) + "\n").encode("utf-8")
        base_source_bytes = bytes(row["base_source_bytes"])
        current = self._catalog.current(policy_id)
        if (
            current is None
            or current.get("version") != row["base_version"]
            or current.get("content_hash") != row["base_hash"]
        ):
            raise PolicyChangeError("draft is stale because the active policy changed")

        try:
            source_now = load_registry(self._source_dir).get(policy_id)
            current_bytes = source_path.read_bytes()
        except (OSError, ValueError) as error:
            raise PolicyChangeError(
                "policy source is unavailable or invalid"
            ) from error
        is_base_source = (
            source_now is not None
            and source_now.get("version") == row["base_version"]
            and source_now.get("content_hash") == row["base_hash"]
        )
        is_already_staged = (
            source_now is not None
            and source_now.get("version") == row["new_version"]
            and source_now.get("content_hash") == row["new_hash"]
        )
        if is_base_source:
            if current_bytes != base_source_bytes:
                raise PolicyChangeError(
                    "policy source formatting changed; refresh and review again"
                )
            self._atomic_write(source_path, proposal_bytes)
        elif not is_already_staged:
            raise PolicyChangeError("draft is stale because the source file changed")

        run_id = uuid.uuid4().hex
        started_at = self._now_iso()
        occurred_at = started_at
        try:
            sources = load_registry(self._source_dir)
            manifest_hash = self._ingestion.manifest_hash(sources)
            report: dict[str, Any] = {}

            def before_commit(
                connection: sqlite3.Connection, sync_result: PolicySyncResult
            ) -> None:
                active = self._catalog.current(policy_id, connection=connection)
                if (
                    active is None
                    or active.get("version") != row["new_version"]
                    or active.get("content_hash") != row["new_hash"]
                ):
                    raise PolicyChangeError(
                        "catalog did not activate the reviewed policy version"
                    )
                verification = self._index.verify_snapshot(
                    policy_id,
                    row["new_version"],
                    self._ingestion.chunks_for_record(proposed),
                    connection=connection,
                )
                if not verification["verified"]:
                    raise PolicyChangeError(
                        "catalog and searchable index do not match the reviewed source"
                    )
                report.update(
                    {
                        "run_id": run_id,
                        "draft_id": draft_id,
                        "status": "succeeded",
                        "policy_id": policy_id,
                        "base_version": row["base_version"],
                        "new_version": row["new_version"],
                        "base_hash": row["base_hash"],
                        "new_hash": row["new_hash"],
                        "manifest_hash": manifest_hash,
                        "started_at": started_at,
                        "completed_at": occurred_at,
                        "changed_fields": json.loads(row["changed_fields_json"])[
                            "fields"
                        ],
                        "diff": row["diff_text"],
                        "counts": {
                            "added": sync_result.added,
                            "changed": sync_result.changed,
                            "removed": sync_result.removed,
                            "unchanged": sync_result.unchanged,
                            "chunks_processed": sync_result.index.processed,
                            "chunks_reused": sync_result.index.reused,
                            "chunks_retired": sync_result.index.retired,
                        },
                        "index_reflected": True,
                        "index_verification": verification,
                    }
                )
                updated = connection.execute(
                    """UPDATE policy_change_drafts
                    SET state='published', published_at=?, published_run_id=?
                    WHERE draft_id=? AND state='reviewed'""",
                    (occurred_at, run_id, draft_id),
                )
                if updated.rowcount != 1:
                    raise PolicyChangeError("draft state changed during publication")
                connection.execute(
                    """INSERT INTO policy_change_runs
                    (run_id, draft_id, policy_id, status, base_version, new_version,
                     started_at, completed_at, source_restored, error_code, report_json)
                    VALUES (?, ?, ?, 'succeeded', ?, ?, ?, ?, NULL, NULL, ?)""",
                    (
                        run_id,
                        draft_id,
                        policy_id,
                        row["base_version"],
                        row["new_version"],
                        started_at,
                        occurred_at,
                        self._json(report),
                    ),
                )

            self._ingestion.sync(
                occurred_at=occurred_at,
                before_commit=before_commit,
                expected_manifest_hash=manifest_hash,
            )
        except Exception as error:
            restored = self._restore_source(
                source_path, proposal_bytes, base_source_bytes
            )
            failure_id = uuid.uuid4().hex
            finished_at = self._now_iso()
            failed_report = {
                "run_id": failure_id,
                "draft_id": draft_id,
                "status": "failed",
                "policy_id": policy_id,
                "base_version": row["base_version"],
                "new_version": row["new_version"],
                "started_at": started_at,
                "completed_at": finished_at,
                "source_restored": restored,
                "error_code": type(error).__name__,
                "message": (
                    "Publish failed; the prior source and active RAG snapshot were restored."
                    if restored
                    else "Publish failed and the source changed concurrently; operator recovery is required."
                ),
            }
            try:
                with self._transaction() as connection:
                    connection.execute(
                        """INSERT INTO policy_change_runs
                        (run_id, draft_id, policy_id, status, base_version, new_version,
                         started_at, completed_at, source_restored, error_code, report_json)
                        VALUES (?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            failure_id,
                            draft_id,
                            policy_id,
                            row["base_version"],
                            row["new_version"],
                            failed_report["started_at"],
                            finished_at,
                            int(restored),
                            type(error).__name__,
                            self._json(failed_report),
                        ),
                    )
            except sqlite3.Error as record_error:
                raise PolicyChangeError(
                    "publish failed and its failure report could not be persisted"
                ) from record_error
            raise PolicyChangeError(failed_report["message"]) from error
        return report

    def _restore_source(
        self, path: Path, proposal_bytes: bytes, base_source_bytes: bytes
    ) -> bool:
        try:
            current = path.read_bytes()
            if current == base_source_bytes:
                return True
            if current != proposal_bytes:
                return False
            self._atomic_write(path, base_source_bytes)
            return path.read_bytes() == base_source_bytes
        except OSError:
            return False

    def list_policies(self) -> list[dict[str, Any]]:
        catalog_records = self._catalog.current_records()
        try:
            sources = load_registry(self._source_dir)
            source_error = None
        except (OSError, ValueError) as error:
            sources = {}
            source_error = type(error).__name__
        outcomes = self._catalog.sync_outcomes()
        results = []
        for policy_id, record in sorted(catalog_records.items()):
            verification = self._index.verify_snapshot(
                policy_id,
                record["version"],
                self._ingestion.chunks_for_record(record),
            )
            source = sources.get(policy_id)
            source_state = (
                "missing"
                if source is None
                else (
                    "aligned"
                    if source.get("version") == record["version"]
                    and source.get("content_hash") == record["content_hash"]
                    else "changed_on_disk"
                )
            )
            source_diff = (
                self._format_diff(record, source)
                if source_state == "changed_on_disk" and source is not None
                else None
            )
            results.append(
                {
                    "policy_id": policy_id,
                    "department_id": record["department_id"],
                    "title": record["title"],
                    "version": record["version"],
                    "status": record["status"],
                    "effective_from": record["effective_from"],
                    "effective_to": record.get("effective_to"),
                    "content_hash": record["content_hash"],
                    "excerpts": record["excerpts"],
                    "outcome": outcomes.get(policy_id, "active"),
                    "source_state": source_state,
                    "source_diff": source_diff,
                    "source_error": source_error,
                    "index_reflected": bool(verification["verified"]),
                    "index_verification": verification,
                }
            )
        return results

    def list_drafts(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT draft_id FROM policy_change_drafts
                WHERE state IN ('draft', 'reviewed') ORDER BY created_at, draft_id"""
            ).fetchall()
        return [self.get_draft(row["draft_id"]) for row in rows]

    def history(self, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded_limit = min(max(int(limit), 1), 100)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT report_json FROM policy_change_runs
                ORDER BY completed_at DESC, run_id DESC LIMIT ?""",
                (bounded_limit,),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def search(
        self, query: str, *, policy_id: str, as_of: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        policy = self._catalog.current(policy_id)
        if policy is None:
            raise PolicyChangeError("unknown policy")
        try:
            date.fromisoformat(as_of)
        except ValueError as error:
            raise PolicyChangeError("as_of must be an ISO calendar date") from error
        evidence = self._retriever.retrieve(
            query,
            departments={policy["department_id"]},
            policy_ids={policy_id},
            as_of=as_of,
            top_k=min(max(int(top_k), 1), 10),
        )
        return [
            {
                "policy_id": item.policy_id,
                "version": item.version,
                "department_id": item.department_id,
                "section_id": item.section_id,
                "text": item.text,
                "citation": item.citation,
                "content_hash": item.content_hash,
                "source_name": item.source_name,
                "effective_from": item.effective_from,
                "effective_to": item.effective_to,
            }
            for item in evidence
        ]
