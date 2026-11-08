"""Minimal LangGraph skeleton; production nodes are added phase by phase.

No node in this graph has direct shell access. Tool execution must go through
validated service adapters and explicit approval policies.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class StudioState(TypedDict, total=False):
    project_id: str
    user_brief: str
    creative_brief: dict
    storyboard: dict
    approved: bool
    status: str
    errors: list[str]


def understand_request(state: StudioState) -> StudioState:
    # Placeholder: connect an approved local Ollama model in Phase 2.
    if not state.get("user_brief"):
        return {"status": "needs_input", "errors": ["user_brief is required"]}
    return {"status": "brief_received"}


def wait_for_approval(state: StudioState) -> StudioState:
    return {"status": "approved" if state.get("approved") else "awaiting_approval"}


def build_graph():
    graph = StateGraph(StudioState)
    graph.add_node("understand_request", understand_request)
    graph.add_node("approval_gate", wait_for_approval)
    graph.add_edge(START, "understand_request")
    graph.add_edge("understand_request", "approval_gate")
    graph.add_edge("approval_gate", END)
    return graph.compile()
