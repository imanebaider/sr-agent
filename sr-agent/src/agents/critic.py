"""Critic / QA agent: checks the report against the evidence and protocol."""
import json
from pathlib import Path

from src.audit import log_event
from src.config import MAX_REVISIONS
from src.llm import ask_structured
from src.schemas import CriticReport
from src.state import State

SYSTEM = (
    "You are a rigorous peer reviewer / QA auditor of AI-written systematic reviews. Check: "
    "(1) every claim is supported by the evidence table; (2) no invented numbers or studies; "
    "(3) the methods match the protocol; (4) risk of bias is reflected in the conclusions; "
    "(5) limitations are honest. Verdict 'revise' only for substantive problems; otherwise 'pass'."
)


def critic_node(state: State) -> dict:
    run_dir = state["run_dir"]
    user = (f"Protocol:\n{json.dumps(state['protocol'], indent=2)}\n\n"
            f"REPORT:\n{state['report_body'][:30000]}")
    rep = ask_structured(CriticReport, SYSTEM, user, run_dir=run_dir,
                         agent="critic", tag=f"revision{state.get('revisions', 0)}")
    data = rep.model_dump()
    revisions = state.get("revisions", 0)
    will_revise = data["verdict"] == "revise" and revisions < MAX_REVISIONS
    data["will_revise"] = will_revise
    (Path(run_dir) / "critic.json").write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    log_event(run_dir, "critic", "verdict", verdict=data["verdict"], score=data["score"],
              will_revise=will_revise)
    return {"critic": data, "revisions": revisions + (1 if will_revise else 0)}
