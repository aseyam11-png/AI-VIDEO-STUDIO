"""Arabic-first creative planning graph with a user approval boundary.

The LLM proposes a typed plan only. Tool execution remains in explicit job services.
"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.integrations.ollama import generate_creative_plan


class StudioState(TypedDict, total=False):
    project_id: str
    user_brief: str
    creative_brief: dict
    storyboard: dict
    approved: bool
    status: str
    errors: list[str]
    target_duration_seconds: int
    language: str


def understand_request(state: StudioState) -> StudioState:
    brief = (state.get("user_brief") or "").strip()
    if not brief:
        return {"status": "needs_input", "errors": ["user_brief is required"]}
    plan = generate_creative_plan(
        brief,
        target_duration_seconds=state.get("target_duration_seconds", 60),
        language=state.get("language", "ar"),
    )
    return {"creative_brief": plan.model_dump(), "status": "plan_created"}


def wait_for_approval(state: StudioState) -> StudioState:
    return {
        "status": "approved" if state.get("approved") else "awaiting_approval",
        "approved": bool(state.get("approved")),
    }


def build_graph():
    graph = StateGraph(StudioState)
    graph.add_node("understand_request", understand_request)
    graph.add_node("approval_gate", wait_for_approval)
    graph.add_edge(START, "understand_request")
    graph.add_edge("understand_request", "approval_gate")
    graph.add_edge("approval_gate", END)
    return graph.compile()
