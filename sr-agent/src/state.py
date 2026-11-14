"""LangGraph shared state."""
from typing import Any, TypedDict


class State(TypedDict, total=False):
    # inputs
    question: str
    protocol_path: str | None
    run_dir: str
    ask_human: bool
    # produced by agents
    protocol: dict
    strategy: dict
    raw_records: list[dict]
    records: list[dict]
    screening: list[dict]
    included: list[dict]
    extractions: list[dict]
    prisma: dict[str, Any]
    report_body: str
    critic: dict
    revisions: int
    metrics: dict[str, Any]
    approved: bool
