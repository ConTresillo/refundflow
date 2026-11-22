"""Bounded simulated decision workflow with human-in-the-loop approval.

The graph is deliberately small and bounded so it can run end to end in a
single queue worker:

1. ``verify_context`` - fail-closed pre-flight. Empty evidence or a fail-closed
   warning stops the run before any proposal is made.
2. ``propose`` - deterministic proposal provider (the mocked LLM boundary).
   It reads only the quoted evidence; it never reads IAM, never mutates
   customer data and never executes a real action.
3. ``await_approval`` - interrupts for one human/agent approval. The resume
   value is either ``approve`` or ``deny``.
4. ``record_decision`` - terminal node that only writes the outcome of the
   exactly-one approval. It never auto-approves.

The checkpointer is optional; when supplied, every node transition is
checkpoint-visible. A restart from an interrupted checkpoint resumes at
the approval node, never re-issuing a proposal.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import StateGraph
from langgraph.types import interrupt

DecisionCode = Literal[
    "queued",
    "proposed",
    "blocked",
    "interrupted",
    "completed",
    "denied",
    "failed",
    "timeout",
    "cancelled",
]

# Names of channels that are safe for UI/editor inspection; runtime-only
# internals are deliberately excluded.
public_channels = frozenset(
    {
        "decision_id",
        "tenant_id",
        "actor_subject_id",
        "question",
        "evidence",
        "proposal",
        "decision",
        "code",
        "error",
        "node_count",
        "step_count",
        "warning_count",
    }
)


class DecisionRunState(TypedDict, total=False):
    """Typed, checkpoint-visible state for one simulated decision run."""

    decision_id: str
    tenant_id: str
    actor_subject_id: str | None
    question: str
    evidence: tuple[dict[str, Any], ...]
    proposal: str | None
    decision: str | None
    code: DecisionCode
    error: str | None
    node_count: int
    step_count: int
    warning_count: int
    # Runtime channels: deadline (fail-closed time budget), tool_call_count, and
    # fail-closed warnings are carried through checkpoint/resume.
    deadline: float | None
    tool_call_count: int
    warnings: tuple[dict[str, Any], ...]


class DecisionProposalProvider:
    """Deterministic proposal provider - the mocked LLM boundary.

    By construction this class never receives an actor, tenant, permission or
    policy-engine handle, and it performs no I/O beyond returning a string.
    It also never resolves to a real action: the proposal is an explicit,
    evidence-anchored recommendation for human review.
    """

    def propose(self, question: str, evidence: Sequence[dict[str, Any]]) -> str:
        if not evidence:
            return "No evidence supplied; no proposal can be made."
        cited = " . ".join(
            f"{item.get('policy_id')}@{item.get('version')}#{item.get('section_id')}"
            for item in evidence
        )
        return (
            f"Decision proposal for: {question}\n"
            f"Reviewed clause(s): {len(evidence)}\n"
            f"Cited clauses: {cited}\n"
            f"Recommendation: treat the quoted policy version as controlling; "
            f"re-check freshness before issuing any refund. "
            f"Explicit human approval is required before a decision is recorded."
        )

    def reset(self) -> None:
        """Idempotent no-op retained for a deterministic, replayable provider."""


@dataclass(frozen=True)
class DecisionBudgets:
    """Bounded execution budgets. All are bounded and fail-closed."""

    max_steps: int = 8
    tool_call_budget: int = 5
    deadline_seconds: float = 15.0


def _now_mono() -> float:
    return time.monotonic()


def _node_verify_context(
    state: DecisionRunState,
    budgets: DecisionBudgets,
) -> DecisionRunState:
    """Fail-closed pre-flight: evidence required, fail-closed warnings stop."""

    node_count = state.get("node_count", 0) + 1
    warnings = list(state.get("warnings") or ())
    fail_closed = [w for w in warnings if w.get("severity") == "fail_closed"]
    if fail_closed:
        return {
            **state,
            "code": "blocked",
            "error": fail_closed[0].get("message", "fail-closed warning"),
            "warning_count": node_count,
        }

    if not state.get("evidence"):
        return {
            **state,
            "code": "blocked",
            "error": "refusing to propose without evidence",
            "warning_count": node_count,
        }

    tool_calls = state.get("tool_call_count", 0)
    if tool_calls >= budgets.tool_call_budget:
        return {
            **state,
            "code": "timeout",
            "error": "tool budget exceeded",
            "warning_count": node_count,
        }

    deadline = state.get("deadline")
    if deadline is not None and _now_mono() > deadline:
        return {
            **state,
            "code": "timeout",
            "error": "deadline exceeded",
            "warning_count": node_count,
        }

    return {
        **state,
        "code": "proposed",
        "node_count": node_count,
        "warning_count": node_count,
    }


def _node_propose(
    state: DecisionRunState,
    provider: DecisionProposalProvider,
) -> DecisionRunState:
    evidence = state.get("evidence", ())
    proposal = provider.propose(state.get("question", ""), evidence)
    return {
        **state,
        "proposal": proposal,
        "code": "proposed",
        "step_count": state.get("step_count", 0) + 1,
    }


def _node_await_approval(state: DecisionRunState) -> DecisionRunState:
    # Interrupt with the allowlisted approval options. On first invocation this
    # raises GraphInterrupt and halts the graph; on resume the value supplied by
    # the waitress is returned and the node re-runs, updating ``decision``.
    decision = interrupt({"options": ["approve", "deny"]})
    return {
        **state,
        "code": "interrupted",
        "decision": decision,
        "step_count": state.get("step_count", 0) + 1,
    }


def _node_record_decision(state: DecisionRunState) -> DecisionRunState:
    """Terminal node: only records the outcome of the exactly-one approval."""

    code = state.get("code")
    decision = state.get("decision")

    if code == "interrupted":
        if decision == "approve":
            code = "completed"
        elif decision == "deny":
            code = "denied"
        else:
            # Fail-closed: an unresolved interrupt never auto-approves.
            code = "failed"
            state = {**state, "error": "decision required before terminal"}
    elif code not in {"completed", "denied", "blocked", "failed", "timeout"}:
        code = "failed"

    return {
        **state,
        "proposal": state.get("proposal"),
        "code": code,
        "decision": decision,
        "node_count": state.get("node_count", 0) + 1,
    }


def build_decision_graph(
    *,
    provider: DecisionProposalProvider | None = None,
    checkpointer: Any | None = None,
) -> Any:
    """Build and compile the bounded decision workflow graph."""

    provider = provider or DecisionProposalProvider()

    def verify_node(state: DecisionRunState) -> DecisionRunState:
        return _node_verify_context(state, DecisionBudgets())

    graph = StateGraph(DecisionRunState)
    graph.add_node("verify", verify_node)
    graph.add_node("propose", lambda s: _node_propose(s, provider))
    graph.add_node("await_approval", lambda s: _node_await_approval(s))
    graph.add_node("record_decision", lambda s: _node_record_decision(s))

    graph.set_entry_point("verify")
    graph.add_conditional_edges("verify", _route_verify)
    graph.add_edge("propose", "await_approval")
    graph.add_edge("await_approval", "record_decision")
    graph.set_finish_point("record_decision")

    return graph.compile(checkpointer=checkpointer)


def _route_verify(state: DecisionRunState) -> str:
    """Route verification results. Only ``proposed`` continues to the proposal
    node; ``blocked`` and ``timeout`` terminate immediately."""
    _debug_route_verify(state)
    if state.get("code") == "proposed":
        return "propose"
    return "record_decision"


def _debug_route_verify(state: DecisionRunState) -> None:
    import os

    if os.environ.get("DECISION_DEBUG") == "1":
        print("DEBUG route_verify repr(state)=", repr(state), flush=True)
