"""Freshness-aware, citation-producing retrieval over local SQLite adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.incremental_index import IndexedChunk, SQLitePolicyIndex
from app.policy_catalog import PolicyCatalog


@dataclass(frozen=True)
class PolicyEvidence:
    """An indexed passage joined to its authoritative policy metadata."""

    policy_id: str
    version: str
    department_id: str
    section_id: str
    text: str
    title: str
    source_name: str
    source_url: str
    source_type: str
    provenance: str
    content_hash: str
    effective_from: str
    effective_to: str | None
    citation: str


class SQLiteEvidenceRetriever:
    """Use FTS for candidates and catalog snapshots as the freshness authority."""

    def __init__(self, index: SQLitePolicyIndex, catalog: PolicyCatalog) -> None:
        self._index = index
        self._catalog = catalog

    def retrieve(
        self,
        query: str,
        *,
        departments: set[str],
        policy_ids: set[str] | None = None,
        as_of: str,
        allowed_policy_ids: set[str] | None = None,
        top_k: int = 5,
    ) -> list[PolicyEvidence]:
        """Return only currently catalogued, active, date-eligible FTS passages."""
        if top_k <= 0 or not departments:
            return []
        try:
            evaluated = date.fromisoformat(as_of)
        except ValueError as error:
            raise ValueError("as_of must be an ISO calendar date") from error
        candidates: list[IndexedChunk] = []
        authorized_ids = allowed_policy_ids
        if authorized_ids == set():
            return []
        for department in sorted(departments):
            candidates.extend(
                self._index.search(
                    query,
                    department_id=department,
                    allowed_policy_ids=authorized_ids,
                    top_k=max(top_k, 1) * 20,
                )
            )
        evidence: list[PolicyEvidence] = []
        seen: set[str] = set()
        for chunk in candidates:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            if policy_ids is not None and chunk.policy_id not in policy_ids:
                continue
            record = self._catalog.current(chunk.policy_id)
            if record is None or record.get("version") != chunk.version:
                continue
            if record.get("department_id") != chunk.department_id or chunk.department_id not in departments:
                continue
            if record.get("status") != "active":
                continue
            try:
                start = date.fromisoformat(record["effective_from"])
                end = date.fromisoformat(record["effective_to"]) if record.get("effective_to") else None
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"invalid effective dates for policy {chunk.policy_id}") from error
            if evaluated < start or (end is not None and evaluated > end):
                continue
            evidence.append(
                PolicyEvidence(
                    policy_id=chunk.policy_id,
                    version=chunk.version,
                    department_id=chunk.department_id,
                    section_id=chunk.section_id,
                    text=chunk.text,
                    title=record.get("title", ""),
                    source_name=record.get("source_name", ""),
                    source_url=record.get("source_url", ""),
                    source_type=record.get("source_type", ""),
                    provenance=record.get("provenance", ""),
                    content_hash=record.get("content_hash", ""),
                    effective_from=record["effective_from"],
                    effective_to=record.get("effective_to"),
                    citation=f"{chunk.policy_id}@{chunk.version}#{chunk.section_id}",
                )
            )
            if len(evidence) >= top_k:
                break
        return evidence
