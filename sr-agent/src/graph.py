"""LangGraph workflow wiring all agents together."""
from langgraph.graph import END, START, StateGraph

from src.agents.critic import critic_node
from src.agents.extraction import extraction_node
from src.agents.planner import planner_node
from src.agents.retrieval import retrieval_node
from src.agents.screening import screening_node
from src.agents.search_strategy import search_strategy_node
from src.agents.synthesis import synthesis_node
from src.audit import log_event
from src.state import State


def human_gate_node(state: State) -> dict:
    """Human-in-the-loop checkpoint after screening (only if --human-review)."""
    if not state.get("ask_human"):
        return {"approved": True}
    inc = state["included"]
    print(f"\n=== HUMAN REVIEW ===\n{len(inc)} studies passed screening "
          f"(kappa={state['metrics'].get('kappa'):.2f}). Details: {state['run_dir']}/screening.json")
    for r in inc[:15]:
        print(f"  - {r['uid']} {r['title'][:90]}")
    ans = input("Continue to extraction & synthesis? [y/N] ").strip().lower()
    approved = ans in {"y", "yes", "o", "oui"}
    log_event(state["run_dir"], "human", "decision", approved=approved)
    return {"approved": approved}


def route_after_gate(state: State) -> str:
    return "continue" if state.get("approved") else "stop"


def route_after_critic(state: State) -> str:
    return "revise" if state["critic"].get("will_revise") else "done"


def build_graph():
    g = StateGraph(State)
    g.add_node("planner", planner_node)
    g.add_node("search_strategy", search_strategy_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("screen", screening_node)
    g.add_node("human_gate", human_gate_node)
    g.add_node("extract", extraction_node)
    g.add_node("synthesize", synthesis_node)
    g.add_node("critique", critic_node)

    g.add_edge(START, "planner")
    g.add_edge("planner", "search_strategy")
    g.add_edge("search_strategy", "retrieval")
    g.add_edge("retrieval", "screen")
    g.add_edge("screen", "human_gate")
    g.add_conditional_edges("human_gate", route_after_gate, {"continue": "extract", "stop": END})
    g.add_edge("extract", "synthesize")
    g.add_edge("synthesize", "critique")
    g.add_conditional_edges("critique", route_after_critic, {"revise": "synthesize", "done": END})
    return g.compile()
