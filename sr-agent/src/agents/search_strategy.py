"""Search Strategy agent: protocol -> Boolean queries (+ MeSH terms)."""
import json
from pathlib import Path

from src.audit import log_event
from src.llm import ask_structured
from src.schemas import SearchStrategy
from src.state import State

SYSTEM = (
    "You design literature-search strategies for systematic reviews. Produce 3 to 5 Boolean "
    "queries compatible with the OpenAlex search box: use uppercase AND / OR, parentheses and "
    "\"quoted phrases\". Cover synonyms and different angles of the PICO; avoid queries so "
    "narrow they return nothing. Also list relevant MeSH terms for documentation."
)


def search_strategy_node(state: State) -> dict:
    run_dir = state["run_dir"]
    user = f"Question: {state['question']}\n\nProtocol:\n{json.dumps(state['protocol'], indent=2)}"
    strat = ask_structured(SearchStrategy, SYSTEM, user, run_dir=run_dir,
                           agent="search_strategy", tag="queries")
    data = strat.model_dump()
    (Path(run_dir) / "search_strategy.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"strategy": data}
