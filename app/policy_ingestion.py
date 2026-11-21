"""Change-detected policy source ingestion with atomic SQLite synchronization."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.document_parsers import (
    DocumentParseError,
    ParsedSection,
    parse_docx,
    parse_md,
    parse_pdf,
    parse_txt,
)
from app.incremental_index import ChunkInput, IngestResult, SQLitePolicyIndex
from app.policies import load_registry
from app.policy_catalog import PolicyCatalog


@dataclass(frozen=True)
class PolicySyncResult:
    """Policy-level change counts and the matching lexical-index work result."""

    added: int
    changed: int
    removed: int
    unchanged: int
    index: IngestResult


class PolicyIngestionService:
    """Reconcile curated JSON snapshots with one authoritative SQLite database.

    Policy versions are immutable: content changes require a new version value.
    Catalog lifecycle writes and index reconciliation share one SQLite
    transaction, so readers see either the old complete snapshot or the new one.
    """

    def __init__(
        self,
        source_dir: str | Path,
        catalog: PolicyCatalog,
        index: SQLitePolicyIndex,
    ) -> None:
        self._source_dir = Path(source_dir)
        catalog_path = Path(catalog.database).resolve()
        index_path = Path(index.path).resolve()
        if catalog_path != index_path:
            raise ValueError(
                "policy ingestion requires catalog and index to share one SQLite database"
            )
        self._catalog = catalog
        self._index = index

    @staticmethod
    def chunks_for_record(record: dict[str, Any]) -> list[ChunkInput]:
        chunks: list[ChunkInput] = []
        for excerpt in record["excerpts"]:
            body = " ".join(excerpt.split())
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            chunks.append(
                {
                    "policy_id": record["id"],
                    "version": record["version"],
                    "department_id": record["department_id"],
                    "section_id": f"excerpt-{digest}",
                    "text": body,
                }
            )
        return chunks

    @staticmethod
    def chunks_for_sections(
        record: dict[str, Any], sections: list[ParsedSection]
    ) -> list[ChunkInput]:
        return [
            {
                "policy_id": record["id"],
                "version": record["version"],
                "department_id": record["department_id"],
                "section_id": section.locator,
                "text": " ".join(section.text.split()),
            }
            for section in sections
        ]

    def replace_pdf(self, path, record, *, occurred_at, tenant_id):
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(record.get("id"), str) or not record.get("id").strip():
            raise ValueError("policy record must include the policy id")
        policy_id = record["id"]
        if not isinstance(policy_id, str) or Path(path).stem not in {policy_id, f"pdf-{policy_id}"}:
            raise ValueError("PDF filename must match policy ID")
        try:
            sections = parse_pdf(path)
        except DocumentParseError as error:
            self._catalog.record_file_status(
                tenant_id, policy_id, status="error", message=type(error).__name__, occurred_at=occurred_at
            )
            raise
        return self._publish_new_version(path, record, occurred_at=occurred_at, tenant_id=tenant_id, sections=sections)

    def replace_docx(self, path, record, *, occurred_at, tenant_id):
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(record.get("id"), str) or not record.get("id").strip():
            raise ValueError("policy record must include the policy id")
        policy_id = record["id"]
        if not isinstance(policy_id, str) or Path(path).stem not in {policy_id, f"pdf-{policy_id}"}:
            raise ValueError("document filename must match policy ID")
        try:
            sections = parse_docx(path)
        except DocumentParseError as error:
            self._catalog.record_file_status(
                tenant_id, policy_id, status="error", message=type(error).__name__, occurred_at=occurred_at
            )
            raise
        return self._publish_new_version(path, record, occurred_at=occurred_at, tenant_id=tenant_id, sections=sections)

    def replace_text(self, path, record, *, occurred_at, tenant_id):
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(record.get("id"), str) or not record.get("id").strip():
            raise ValueError("policy record must include the policy id")
        policy_id = record["id"]
        if not isinstance(policy_id, str) or Path(path).stem != policy_id:
            raise ValueError("text filename must match policy ID")
        try:
            sections = parse_txt(path)
        except DocumentParseError as error:
            self._catalog.record_file_status(
                tenant_id, policy_id, status="error", message=type(error).__name__, occurred_at=occurred_at
            )
            raise
        return self._publish_new_version(path, record, occurred_at=occurred_at, tenant_id=tenant_id, sections=sections)

    def replace_markdown(self, path, record, *, occurred_at, tenant_id):
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(record.get("id"), str) or not record.get("id").strip():
            raise ValueError("policy record must include the policy id")
        policy_id = record["id"]
        if not isinstance(policy_id, str) or Path(path).stem != policy_id:
            raise ValueError("markdown filename must match policy ID")
        try:
            sections = parse_md(path)
        except DocumentParseError as error:
            self._catalog.record_file_status(
                tenant_id, policy_id, status="error", message=type(error).__name__, occurred_at=occurred_at
            )
            raise
        return self._publish_new_version(path, record, occurred_at=occurred_at, tenant_id=tenant_id, sections=sections)

    def replace_document(self, path, record, *, occurred_at, tenant_id):
        """Dispatch an upload to the matching format-specific replacement."""
        suffix = Path(path).suffix.lower()
        if suffix == ".pdf":
            return self.replace_pdf(path, record, occurred_at=occurred_at, tenant_id=tenant_id)
        if suffix == ".docx":
            return self.replace_docx(path, record, occurred_at=occurred_at, tenant_id=tenant_id)
        if suffix in (".txt", ".md"):
            return self.replace_text(path, record, occurred_at=occurred_at, tenant_id=tenant_id) if suffix == ".txt" else self.replace_markdown(path, record, occurred_at=occurred_at, tenant_id=tenant_id)
        raise ValueError(f"unsupported document format: {suffix or '<empty>'}")

    def _publish_new_version(self, path, record, *, occurred_at, tenant_id, sections):
        policy_id = record["id"]
        chunks = self.chunks_for_sections(record, sections)
        try:
            with self._catalog.transaction() as connection:
                previous = self._catalog.current(policy_id, connection=connection)
                if previous is not None and record.get("status") != "active":
                    raise ValueError("policy version must be active")
                if previous is not None and self._catalog.version(policy_id, record["version"], connection=connection):
                    raise ValueError("policy version is immutable")
                if previous is None:
                    self._catalog.publish(record, occurred_at=occurred_at, connection=connection)
                    added, changed = 1, 0
                else:
                    self._catalog.supersede(policy_id, previous["version"], occurred_at=occurred_at, connection=connection)
                    self._catalog.publish(record, occurred_at=occurred_at, connection=connection)
                    added, changed = 0, 1
                complete = {(policy_id, version["version"]) for version in self._catalog.versions(policy_id, connection=connection)}
                index_result = self._index.ingest_in_transaction(connection, chunks, complete_versions=complete)
                self._catalog.record_sync_outcomes({policy_id: "added" if added else "changed"}, occurred_at=occurred_at, connection=connection)
            self._catalog.record_file_status(tenant_id, policy_id, status="indexed", message="document indexed", occurred_at=occurred_at)
            return PolicySyncResult(added, changed, 0, 0, index_result)
        except (ValueError, OSError) as error:
            self._catalog.record_file_status(tenant_id, policy_id, status="error", message=type(error).__name__, occurred_at=occurred_at)
            raise

    @staticmethod
    def manifest_hash(sources: dict[str, dict[str, Any]]) -> str:
        """Hash sorted policy IDs and canonical content hashes for a source snapshot."""
        material = "\n".join(
            f"{policy_id}\0{record['content_hash']}"
            for policy_id, record in sorted(sources.items())
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def status(self) -> list[dict[str, str]]:
        """Return current policy provenance and latest sync outcomes, including failed files."""
        records = self._catalog.current_records()
        outcomes = self._catalog.sync_outcomes()
        for policy_id, outcome in outcomes.items():
            if outcome.startswith("error:") and policy_id not in records:
                records[policy_id] = {
                    "version": "", "department_id": "", "status": "error",
                    "content_hash": "", "provenance": "",
                }
        return [
            {
                "policy_id": policy_id,
                "version": record["version"],
                "department_id": record["department_id"],
                "status": record["status"],
                "content_hash": record["content_hash"],
                "provenance": record["provenance"],
                "outcome": outcomes.get(policy_id, "active"),
            }
            for policy_id, record in sorted(records.items())
        ]

    def sync(
        self,
        *,
        occurred_at: str,
        before_commit: Callable[[sqlite3.Connection, PolicySyncResult], None]
        | None = None,
        expected_manifest_hash: str | None = None,
    ) -> PolicySyncResult:
        """Apply added, versioned, unchanged, and removed source files atomically."""
        if not isinstance(occurred_at, str) or not occurred_at.strip():
            raise ValueError("occurred_at must be an ISO date or timestamp")
        try:
            sync_date = datetime.fromisoformat(
                occurred_at.replace("Z", "+00:00")
            ).date()
        except ValueError as error:
            raise ValueError("occurred_at must be an ISO date or timestamp") from error

        sources = load_registry(self._source_dir)
        snapshot_hash = self.manifest_hash(sources)
        if (
            expected_manifest_hash is not None
            and snapshot_hash != expected_manifest_hash
        ):
            raise ValueError(
                "policy source changed after its preview; refresh and review again"
            )
        for record in sources.values():
            if record.get("status") != "active":
                raise ValueError("source files must describe active policy versions")
            try:
                effective_from = date.fromisoformat(record["effective_from"])
                effective_to = (
                    date.fromisoformat(record["effective_to"])
                    if record.get("effective_to") is not None
                    else None
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid effective dates for policy {record.get('id')}"
                ) from error
            if effective_to is not None and effective_to < effective_from:
                raise ValueError(
                    f"effective_to precedes effective_from for policy {record['id']}"
                )
            if effective_from > sync_date:
                raise ValueError(
                    f"future effective policy version cannot be activated by source sync: {record['id']}"
                )

        added = changed = removed = unchanged = 0
        chunks: list[ChunkInput] = []
        complete_versions: set[tuple[str, str]] = set()
        outcomes: dict[str, str] = {}
        with self._catalog.transaction() as connection:
            current = self._catalog.current_records(connection=connection)

            for policy_id, record in sources.items():
                previous = current.get(policy_id)
                if previous is None:
                    if self._catalog.version(
                        policy_id, record["version"], connection=connection
                    ):
                        raise ValueError(
                            f"policy version is immutable: {policy_id}@{record['version']}"
                        )
                    self._catalog.publish(
                        record, occurred_at=occurred_at, connection=connection
                    )
                    added += 1
                    outcomes[policy_id] = "added"
                    chunks.extend(self.chunks_for_record(record))
                    continue

                if previous["version"] == record["version"]:
                    if previous["status"] != "active":
                        raise ValueError(
                            f"source points to inactive version {policy_id}@{record['version']}; publish a new version"
                        )
                    if previous["content_hash"] != record["content_hash"]:
                        raise ValueError(
                            f"content changed without a version change for {policy_id}; version must change"
                        )
                    unchanged += 1
                    outcomes[policy_id] = "unchanged"
                    continue

                if self._catalog.version(
                    policy_id, record["version"], connection=connection
                ):
                    raise ValueError(
                        f"policy version is immutable: {policy_id}@{record['version']}"
                    )
                if previous["status"] == "active":
                    self._catalog.supersede(
                        policy_id,
                        previous["version"],
                        occurred_at=occurred_at,
                        connection=connection,
                    )
                self._catalog.publish(
                    record, occurred_at=occurred_at, connection=connection
                )
                changed += 1
                outcomes[policy_id] = "changed"
                complete_versions.update(
                    (policy_id, version["version"])
                    for version in self._catalog.versions(
                        policy_id, connection=connection
                    )
                )
                chunks.extend(self.chunks_for_record(record))

            for policy_id, previous in current.items():
                if policy_id in sources:
                    continue
                versions = self._catalog.versions(policy_id, connection=connection)
                complete_versions.update(
                    (policy_id, version["version"]) for version in versions
                )
                if previous["status"] == "active":
                    self._catalog.withdraw(
                        policy_id,
                        occurred_at=occurred_at,
                        reason="source file removed",
                        connection=connection,
                    )
                    removed += 1
                    outcomes[policy_id] = "removed"

            if chunks or complete_versions:
                index_result = self._index.ingest_in_transaction(
                    connection,
                    chunks,
                    complete_versions=complete_versions,
                )
            else:
                index_result = IngestResult(0, 0, 0, ())
            result = PolicySyncResult(added, changed, removed, unchanged, index_result)
            if before_commit:
                before_commit(connection, result)
            if self.manifest_hash(load_registry(self._source_dir)) != snapshot_hash:
                raise ValueError(
                    "policy source changed during sync; retry from a fresh preview"
                )
            self._catalog.record_sync_outcomes(
                outcomes, occurred_at=occurred_at, connection=connection
            )

        return result
