"""Async LangGraph evidence pipeline with durable SQLite checkpointing.

The pipeline is deliberately small and bounded: authorize -> retrieve ->
terminal evidence. It is a real LangGraph state graph (typed channels,
node/edge boundaries, checkpointed in SQLite via langgraph-checkpoint), and
synchronous SQLite reads are turned off the event loop with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph

from app.evidence_run import (
    EvidenceRunRepository,
    EvidenceRunResult,
    EvidenceRunState,
    evidence_from_dicts,
    evidence_to_dicts,
)
from app.retrieval import SQLiteEvidenceRetriever


def _route_after_authorize(state: EvidenceRunState) -> str:
    return "terminal" if state["code"] == "denied" else "retrieve"


def build_evidence_graph(
    retriever: SQLiteEvidenceRetriever,
    scope: Callable[..., set[str]],
    *,
    checkpointer: SqliteSaver | None = None,
) -> Any:
    """Build the authorize -> retrieve -> terminal evidence graph."""

    async def _authorize_node(state: EvidenceRunState) -> EvidenceRunState:
        """Reauthorize against the caller-supplied scope. Deny-by-default."""
        allowed = await asyncio.to_thread(
            scope,
            tenant_id=state["tenant_id"],
            actor_subject_id=state["actor_subject_id"],
            departments=state["departments"],
        )
        if not allowed:
            return {
                "code": "denied",
                "error": "authorization denied",
                "evidence": (),
            }
        return {
            "code": "authorized",
            "error": None,
            "evidence": (),
        }

    async def _retrieve_node(state: EvidenceRunState) -> EvidenceRunState:
        """Real lexical retrieval over the local SQLite FTS5 index."""
        try:
            evidence = await asyncio.to_thread(
                retriever.retrieve,
                state["query"],
                departments=set(state["departments"]),
                allowed_policy_ids=set(state["allowed_policy_ids"]),
                as_of=state["as_of"],
                evidence_mode=state["evidence_mode"],
                answer_cache=state["answer_cache"],
                top_k=5,
            )
        except Exception as error:  # noqa: BLE001 - normalize retrieval failures for bounded retry
            return {
                "code": "failed",
                "error": f"retrieval failed: {type(error).__name__}: {error}",
                "evidence": (),
            }
        return {
            "code": "retrieved",
            "error": None,
            "evidence": evidence_to_dicts(evidence),
        }

    def _terminal_node(state: EvidenceRunState) -> EvidenceRunState:
        """Assemble the terminal result from the retrieved evidence."""
        code = "completed" if state["code"] != "denied" else "denied"
        return {
            "code": code,
            "evidence": state.get("evidence", ()),
            "error": state.get("error"),
        }

    graph = StateGraph(EvidenceRunState)
    graph.add_node("authorize", _authorize_node)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("terminal", _terminal_node)
    graph.set_entry_point("authorize")
    graph.add_conditional_edges("authorize", _route_after_authorize)
    graph.add_edge("retrieve", "terminal")
    graph.set_finish_point("terminal")
    return graph.compile(checkpointer=checkpointer)


class EvidenceRunner:
    """Runs one async evidence workflow per run against a durable checkpoint.

    The graph is built once and shared by all runs on the same database, so the
    SQLite checkpointer (langgraph-checkpoint) records every node transition for
    every run. A fresh runner instance with the same checkpointer can read saved
    checkpoint state back, which is how the queue recovers after a loss.
    """

    def __init__(
        self,
        *,
        repository: EvidenceRunRepository,
        graph: Any | None = None,
        checkpointer: SqliteSaver | None = None,
        retriever: SQLiteEvidenceRetriever | None = None,
        scope: Callable[..., set[str]] | None = None,
    ) -> None:
        self._repository = repository
        self._checkpointer = checkpointer
        if graph is None:
            if retriever is None or scope is None:
                raise ValueError("graph requires retriever and scope")
            graph = build_evidence_graph(
                retriever=retriever,
                scope=scope,
                checkpointer=checkpointer,
            )
        self._graph = graph
        self._retriever = retriever
        self._scope = scope


    async def run(self, run_id: str) -> EvidenceRunResult:
        row = self._repository.status(run_id)
        state = self._to_state(row)
        # Compute the allowed policy IDs from the fail-closed scope so the
        # retrieve node can enforce tenant/document grants before ranking.
        scope = self._scope
        state["allowed_policy_ids"] = (
            scope(
                tenant_id=state["tenant_id"],
                actor_subject_id=state["actor_subject_id"],
                departments=state["departments"],
            )
            if scope is not None
            else set()
        )
        run_id = state.get("run_id")
        if run_id is None:
            raise ValueError("evidence run state is missing run_id")
        config = {"configurable": {"thread_id": run_id}}
        final_state = await self._graph.ainvoke(state, config=config)
        return EvidenceRunResult(
            run_id=run_id,
            status=final_state.get("code", "failed"),
            evidence=evidence_from_dicts(final_state.get("evidence", ())),
            error=final_state.get("error"),
        )

    def read_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        config = {"configurable": {"thread_id": run_id}}
        saved = self._graph.get_state(config)
        if saved is None:
            return None
        return {
            "run_id": saved.config.get("configurable", {}).get("run_id"),
            "code": saved.values.get("code"),
            "evidence_count": len(saved.values.get("evidence", ())),
            "error": saved.values.get("error"),
        }

    @staticmethod
    def _to_state(row: Any) -> EvidenceRunState:
        if hasattr(row, "model_dump"):
            # EvidenceRun pydantic model (from repository status()); fields already
            # match the graph state shape.
            return {
                "run_id": row.run_id,
                "tenant_id": row.tenant_id,
                "actor_subject_id": row.actor_subject_id,
                "query": row.query,
                "departments": row.departments,
                "as_of": row.as_of,
                "evidence_mode": row.evidence_mode,
                "answer_cache": row.answer_cache,
                "code": "queued",
                "evidence": (),
                "error": None,
            }
        # sqlite3.Row or dict from the repository.
        return {
            "run_id": row["run_id"],
            "tenant_id": row["tenant_id"],
            "actor_subject_id": row["actor_subject_id"],
            "query": row["query"],
            "departments": tuple(json.loads(row["departments_json"]) or ()),
            "as_of": row["as_of"],
            "evidence_mode": row["evidence_mode"],
            "answer_cache": row["answer_cache"],
            "code": "queued",
            "evidence": (),
            "error": None,
        }



