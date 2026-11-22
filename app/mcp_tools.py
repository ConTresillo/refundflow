"""Read-only MCP tool adapters for RefundFlow.

Only three tools are published, and all three are read-only by construction:

* ``get_policy_document`` - inspect one published policy version.
* ``list_policy_timeline`` - list publication/withdrawal/expiry events.
* ``query_local_evidence`` - retrieve already-cited evidence (no write path).

No tool performs a mutation. Argument tampering cannot escalate because the
tool handler validates the exact argument names and raises ``McpToolError``
before any business logic runs; an unknown or extra argument is rejected, not
mapped onto a different operation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.policy_catalog import PolicyCatalog

logger = logging.getLogger(__name__)

# The complete, allowlisted tool surface. Anything outside this set is
# deliberately absent; do not add tools to this set without a reviewed change.
_READ_ONLY_TOOLS = frozenset(
    {
        "get_policy_document",
        "list_policy_timeline",
        "query_local_evidence",
    }
)

_READ_ONLY_NAMES = frozenset(_READ_ONLY_TOOLS)


class McpToolError(RuntimeError):
    """A rejected MCP tool call (validation, not business outcome)."""


def _require_exact_args(args: dict[str, Any], *, allowed: frozenset[str]) -> None:
    unexpected = sorted(set(args) - allowed)
    if unexpected:
        raise McpToolError(
            f"unexpected arguments: {', '.join(unexpected)}; allowed: {sorted(allowed)}"
        )


def _index_date(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpToolError("date argument must be a non-empty ISO date")
    return value.strip()


def _record_or_none(record: dict[str, Any] | None) -> dict[str, Any]:
    if record is None:
        raise McpToolError("policy not found or not active")
    return record


def create_mcp_server(
    *,
    catalog: PolicyCatalog,
    source_dir: Path | None = None,
) -> FastMCP:
    """Build a FastMCP server with exactly the allowlisted read-only tools."""

    source_dir = None if source_dir is None else Path(source_dir).resolve()
    server = FastMCP("refundflow-policy-tools")

    @server.tool(name="get_policy_document", description="Return one published policy version.")
    def get_policy_document(
        policy_id: str,
        *,
        as_of: str = "1970-01-01",
    ) -> dict[str, Any]:
        _require_exact_args(
            {"policy_id": policy_id, "as_of": as_of},
            allowed=frozenset({"policy_id", "as_of"}),
        )
        record = catalog.current(policy_id)
        return _record_or_none(record)

    @server.tool(name="list_policy_timeline", description="Return publication/withdrawal/expiry events.")
    def list_policy_timeline(
        *,
        department_id: str | None = None,
        as_of: str = "1970-01-01",
    ) -> list[dict[str, Any]]:
        _require_exact_args(
            {"department_id": department_id, "as_of": as_of},
            allowed=frozenset({"department_id", "as_of"}),
        )
        events = catalog.timeline()
        if department_id:
            events = [dict(e) for e in events if e.get("department_id") == department_id]
        return events

    @server.tool(name="query_local_evidence", description="Return authorized policy evidence (document snapshot).")
    def query_local_evidence(
        query: str,
        *,
        departments: list[str],
        as_of: str,
        evidence_mode: str,
        answer_cache: str,
    ) -> list[dict[str, Any]]:
        _require_exact_args(
            {
                "query": query,
                "departments": departments,
                "as_of": as_of,
                "evidence_mode": evidence_mode,
                "answer_cache": answer_cache,
            },
            allowed=frozenset({"query", "departments", "as_of", "evidence_mode", "answer_cache"}),
        )
        if not departments:
            raise McpToolError("departments must not be empty")
        if evidence_mode not in ("freshness_preferred", "ttl_snapshot", "critical"):
            raise McpToolError(f"unknown evidence_mode: {evidence_mode!r}")
        if answer_cache not in ("off", "exact"):
            raise McpToolError(f"unknown answer_cache: {answer_cache!r}")
        records = catalog.current_records()
        # Document-level evidence: current active snapshots for the requested
        # departments only. Secondary stores lead, not writes.
        items: list[dict[str, Any]] = []
        for record in records.values():
            if record.get("department_id") in departments:
                items.append(
                    {
                        "policy_id": record.get("id"),
                        "version": record.get("version"),
                        "department_id": record.get("department_id"),
                        "title": record.get("title"),
                        "status": record.get("status"),
                        "effective_from": record.get("effective_from"),
                        "effective_to": record.get("effective_to"),
                        "query_context": query,
                        "as_of": as_of,
                        "evidence_mode": evidence_mode,
                        "answer_cache": answer_cache,
                    }
                )
        return items

    return server
