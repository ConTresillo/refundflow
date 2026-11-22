"""Protocol-level tests for the read-only RefundFlow MCP tool surface (P6a).

Exercises the actual FastMCP-registered handlers through the server's
``list_tools`` / ``call_tool`` surface: the allowlist, argument validation and
tool behaviour are verified end to end, not just the module-level helpers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.mcp_tools import create_mcp_server


class _StubCatalog:
    """Minimal duck-typed stand-in for the SQLite PolicyCatalog."""

    def __init__(self, records: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
        self._records = records
        self._events = list(events)

    def current(self, policy_id: str) -> dict[str, Any] | None:
        for record in self._records:
            if record.get("id") == policy_id:
                return dict(record)
        return None

    def timeline(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events]

    def current_records(self) -> dict[str, dict[str, Any]]:
        return {record["id"]: dict(record) for record in self._records}


@pytest.fixture()
def catalog() -> _StubCatalog:
    return _StubCatalog(
        records=[
            {
                "id": "billing-refunds",
                "version": "v77.7",
                "department_id": "billing",
                "title": "Refund redemptions",
                "status": "active",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "content_hash": "hash-billing-v77.7",
            }
        ],
        events=[
            {
                "event_type": "published",
                "policy_id": "billing-refunds",
                "version": "v77.7",
                "department_id": "billing",
                "occurred_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    )


def _structured(result: Any) -> list[dict[str, Any]]:
    """Extract structured payloads from FastMCP's wrapped tool result."""
    outer = result[1]
    if isinstance(outer, dict) and "result" in outer:
        return outer["result"]
    if isinstance(outer, list):
        return outer
    return [outer]


def test_tool_registry_is_exact_allowlist(catalog):
    server = create_mcp_server(catalog=catalog)
    names = [tool.name for tool in asyncio.run(server.list_tools())]
    assert names == [
        "get_policy_document",
        "list_policy_timeline",
        "query_local_evidence",
    ]


def test_get_policy_document_returns_current_version(catalog):
    server = create_mcp_server(catalog=catalog)
    items = _structured(
        asyncio.run(
            server.call_tool(
                "get_policy_document",
                arguments={"policy_id": "billing-refunds", "as_of": "2026-11-22"},
            )
        )
    )
    assert items[0]["id"] == "billing-refunds"
    assert items[0]["version"] == "v77.7"


def test_get_policy_document_rejects_unknown_policy(catalog):
    server = create_mcp_server(catalog=catalog)
    with pytest.raises(Exception):
        asyncio.run(
            server.call_tool(
                "get_policy_document",
                arguments={"policy_id": "shipping-refunds", "as_of": "2026-11-22"},
            )
        )


def test_list_policy_timeline_filters_by_department(catalog):
    server = create_mcp_server(catalog=catalog)
    events = _structured(
        asyncio.run(
            server.call_tool(
                "list_policy_timeline",
                arguments={"department_id": "billing", "as_of": "2026-11-22"},
            )
        )
    )
    assert [e["department_id"] for e in events] == ["billing"]


def test_query_local_evidence_returns_department_only(catalog):
    server = create_mcp_server(catalog=catalog)
    items = _structured(
        asyncio.run(
            server.call_tool(
                "query_local_evidence",
                arguments={
                    "query": "refund",
                    "departments": ["billing"],
                    "as_of": "2026-11-22",
                    "evidence_mode": "freshness_preferred",
                    "answer_cache": "off",
                },
            )
        )
    )
    assert [i["department_id"] for i in items] == ["billing"]


def test_unknown_tool_is_rejected(catalog):
    server = create_mcp_server(catalog=catalog)
    with pytest.raises(Exception):
        asyncio.run(server.call_tool("bogus_tool", arguments={}))


def test_unknown_evidence_mode_is_rejected(catalog):
    server = create_mcp_server(catalog=catalog)
    with pytest.raises(Exception):
        asyncio.run(
            server.call_tool(
                "query_local_evidence",
                arguments={
                    "query": "refund",
                    "departments": ["billing"],
                    "as_of": "2026-11-22",
                    "evidence_mode": "bogus",
                    "answer_cache": "off",
                },
            )
        )
