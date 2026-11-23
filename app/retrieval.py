"""Freshness-aware, citation-producing retrieval over local SQLite adapters."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.answer_cache import (
    AnswerCache,
    CacheKey,
    SqliteBoundedAnswerCache,
)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "department_id": self.department_id,
            "section_id": self.section_id,
            "text": self.text,
            "title": self.title,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "provenance": self.provenance,
            "content_hash": self.content_hash,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "citation": self.citation,
        }


class SQLiteEvidenceRetriever:
    """Use FTS for candidates and catalog snapshots as the freshness authority."""

    FRESHNESS_MODES = ("freshness_preferred", "ttl_snapshot", "critical")
    ANSWER_CACHE_MODES = ("off", "exact")

    @dataclass(frozen=True)
    class CacheEntry:
        fingerprint: str
        items: tuple[dict[str, Any], ...]
        snapshot_fingerprint: str
        expires_at: float

    def __init__(
        self,
        index: SQLitePolicyIndex,
        catalog: PolicyCatalog,
        *,
        evidence_mode: str = "freshness_preferred",
        answer_cache: str = "off",
        cache_ttl_seconds: float = 300.0,
        source_check_max_age_seconds: float = 60.0,
        max_cache_entries: int = 256,
        cache: AnswerCache | None = None,
    ) -> None:
        self._index = index
        self._catalog = catalog
        self.evidence_mode = self._require_evidence_mode(evidence_mode)
        self.answer_cache = answer_cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._source_check_max_age_seconds = source_check_max_age_seconds
        self._max_cache_entries = max_cache_entries
        self._cache: dict[str, self.CacheEntry] = {}
        self._last_cache_status: str = ""
        self._last_source_age_seconds: int = -1
        self._answer_cache = cache or SqliteBoundedAnswerCache(catalog.database)

    def _require_evidence_mode(self, evidence_mode: str) -> str:
        if evidence_mode not in self.FRESHNESS_MODES:
            raise ValueError(f"evidence_mode must be one of {self.FRESHNESS_MODES}")
        return evidence_mode

    def _fingerprint(
        self,
        *,
        query: str,
        as_of: str,
        departments: set[str],
        allowed_policy_ids: set[str] | None,
        evidence_mode: str,
        answer_cache: str,
    ) -> str:
        material = "\0".join(
            [
                query,
                as_of,
                ",".join(sorted(departments)),
                str(sorted(allowed_policy_ids or ())),
                evidence_mode,
                answer_cache,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _catalog_snapshot_fingerprint(self) -> str:
        records = self._catalog.current_records()
        material = json.dumps(records, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _source_age_seconds(self, as_of: str) -> int:
        rows = self._catalog.source_watch_status()
        if not rows:
            return -1
        try:
            evaluated = date.fromisoformat(as_of)
        except ValueError:
            evaluated = date.today()
        ages: list[int] = []
        for row in rows.values():
            last_success = row.get("last_success_at")
            if not last_success:
                continue
            try:
                last = date.fromisoformat(last_success)
            except ValueError:
                continue
            ages.append((evaluated - last).days * 86400 + (evaluated - last).seconds)
        return max(ages) if ages else -1

    def _freshness_state(self, source_age_seconds: int, cache_status: str) -> str:
        if cache_status == "bypassed":
            return "critical"
        if source_age_seconds < 0:
            return "unknown"
        if source_age_seconds <= self._source_check_max_age_seconds:
            return "fresh"
        return "freshness_pending"

    def _build_evidence(
        self,
        candidates: list[IndexedChunk],
        allowed_policy_ids: set[str],
        as_of: str,
        envelope_fingerprint: str,
        cache_status: str,
        top_k: int,
        policy_ids: set[str] | None = None,
    ) -> list[PolicyEvidence]:
        seen: set[str] = set()
        evidence: list[PolicyEvidence] = []
        evaluated = date.fromisoformat(as_of)
        for chunk in candidates:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            if allowed_policy_ids and chunk.policy_id not in allowed_policy_ids:
                continue
            if policy_ids is not None and chunk.policy_id not in policy_ids:
                continue
            record = self._catalog.current(chunk.policy_id)
            if record is None or record.get("version") != chunk.version:
                continue
            if record.get("department_id") != chunk.department_id:
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

    def _trim_cache(self) -> None:
        while len(self._cache) > self._max_cache_entries:
            oldest_fingerprint, _ = next(iter(self._cache.items()))
            del self._cache[oldest_fingerprint]

    def retrieve(
        self,
        query: str,
        *,
        departments: set[str],
        policy_ids: set[str] | None = None,
        as_of: str,
        allowed_policy_ids: set[str] | None = None,
        top_k: int = 5,
        evidence_mode: str = "freshness_preferred",
        answer_cache: str = "off",
        subject_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[PolicyEvidence]:
        """Return only currently catalogued, active, date-eligible FTS passages.

        ``evidence_mode`` selects the freshness contract. ``critical`` bypasses
        any answer reuse and revalidates source availability before output.
        ``ttl_snapshot`` consults a bounded TTL cache whose key includes the
        catalog snapshot fingerprint; a catalog change invalidates cached
        answers. ``freshness_preferred`` revalidates, discloses source
        freshness, and never reuses a stale envelope.
        """
        if top_k <= 0 or not departments:
            return []
        if answer_cache not in self.ANSWER_CACHE_MODES:
            raise ValueError(f"answer_cache must be one of {self.ANSWER_CACHE_MODES}")
        self._require_evidence_mode(evidence_mode)
        try:
            date.fromisoformat(as_of)
        except ValueError as error:
            raise ValueError("as_of must be an ISO calendar date") from error
        if allowed_policy_ids == set():
            return []

        candidates: list[IndexedChunk] = []
        for department in sorted(departments):
            candidates.extend(
                self._index.search(
                    query,
                    department_id=department,
                    allowed_policy_ids=allowed_policy_ids,
                    top_k=max(top_k, 1) * 20,
                )
            )

        envelope_fingerprint = self._fingerprint(
            query=query,
            as_of=as_of,
            departments=departments,
            allowed_policy_ids=allowed_policy_ids,
            evidence_mode=evidence_mode,
            answer_cache=answer_cache,
        )

        if evidence_mode == "critical":
            cache_status = "bypassed"
            items = self._build_evidence(
                candidates, allowed_policy_ids or set(), as_of, envelope_fingerprint, cache_status, top_k
            )
        elif answer_cache == "exact" and self._answer_cache is not None:
            cache_key = CacheKey(
                query=query,
                departments=departments,
                as_of=as_of,
                allowed_policy_ids=allowed_policy_ids,
                evidence_mode=evidence_mode,
                answer_cache=answer_cache,
                auth_subject=subject_id or "",
                auth_tenant=tenant_id or "",
                snapshot_hash=self._catalog_snapshot_fingerprint(),
            )
            cached = self._answer_cache.get(cache_key)
            if cached is not None:
                items = [PolicyEvidence(**item) for item in cached[0]]
                cache_status = "exact_hit"
            else:
                cache_status = "miss"
                items = self._build_evidence(
                    candidates, allowed_policy_ids or set(), as_of, envelope_fingerprint, cache_status, top_k
                )
                self._answer_cache.put(
                    cache_key,
                    [item.to_dict() for item in items],
                    snapshot_hash=cache_key.snapshot_hash,
                    auth_hash=f"{subject_id or ''}\0{tenant_id or ''}",
                    ttl_seconds=self._cache_ttl_seconds,
                    size_bytes=sum(len(json.dumps(item.to_dict(), sort_keys=True).encode("utf-8")) for item in items),
                )
        elif evidence_mode == "ttl_snapshot" and answer_cache != "off":
            entry = self._cache.get(envelope_fingerprint)
            if (
                entry is not None
                and entry.expires_at > time.monotonic()
                and entry.snapshot_fingerprint == self._catalog_snapshot_fingerprint()
            ):
                cache_status = "exact_hit"
                self._last_cache_status = cache_status
                items = [PolicyEvidence(**item) for item in entry.items]
            else:
                cache_status = "miss"
                items = self._build_evidence(
                    candidates, allowed_policy_ids or set(), as_of, envelope_fingerprint, cache_status, top_k
                )
                self._cache[envelope_fingerprint] = self.CacheEntry(
                    fingerprint=envelope_fingerprint,
                    items=tuple(
                        {
                            "policy_id": item.policy_id,
                            "version": item.version,
                            "department_id": item.department_id,
                            "section_id": item.section_id,
                            "text": item.text,
                            "title": item.title,
                            "source_name": item.source_name,
                            "source_url": item.source_url,
                            "source_type": item.source_type,
                            "provenance": item.provenance,
                            "content_hash": item.content_hash,
                            "effective_from": item.effective_from,
                            "effective_to": item.effective_to,
                            "citation": item.citation,
                        }
                        for item in items
                    ),
                    snapshot_fingerprint=self._catalog_snapshot_fingerprint(),
                    expires_at=time.monotonic() + self._cache_ttl_seconds,
                )
                self._trim_cache()
        else:
            cache_status = "off"
            items = self._build_evidence(
                candidates, allowed_policy_ids or set(), as_of, envelope_fingerprint, cache_status, top_k
            )
        self._last_cache_status = cache_status
        self._last_source_age_seconds = self._source_age_seconds(as_of)
        return items
