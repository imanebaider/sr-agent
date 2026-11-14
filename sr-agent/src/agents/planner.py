"""Planner agent: research question -> PRISMA-style protocol (or load a YAML template)."""
from pathlib import Path

import yaml

from src.audit import log_event
from src.llm import ask_structured
from src.schemas import Protocol
from src.state import State

SYSTEM = (
    "You are an expert systematic-review methodologist (PRISMA 2020, Cochrane). "
    "Turn a research question into a rigorous review protocol: a PICO framework, explicit "
    "and testable inclusion/exclusion criteria, keywords and eligible study designs."
)


def planner_node(state: State) -> dict:
    run_dir = state["run_dir"]
    path = state.get("protocol_path")
    if path:
        proto = Protocol(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))
        log_event(run_dir, "planner", "protocol_loaded_from_template", path=path)
    else:
        proto = ask_structured(
            Protocol, SYSTEM, f"Research question: {state['question']}",
            run_dir=run_dir, agent="planner", tag="protocol")
        log_event(run_dir, "planner", "protocol_generated")
    data = proto.model_dump()
    (Path(run_dir) / "protocol.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"protocol": data}
