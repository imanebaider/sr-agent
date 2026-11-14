"""Retrieval agent: run the queries, merge sources, deduplicate, build PRISMA counts."""
import json
from itertools import zip_longest
from pathlib import Path

from src.audit import log_event
from src.config import MAX_RECORDS, OPENALEX_EMAIL
from src.state import State
from src.tools.dedup import deduplicate
from src.tools.openalex import search_openalex


def retrieval_node(state: State) -> dict:
    run_dir = state["run_dir"]
    queries = state["strategy"]["queries"]
    year_from = state["protocol"].get("year_from")
    per_query = max(20, MAX_RECORDS)

    per_query_results = []
    for q in queries:
        try:
            res = search_openalex(q, n=per_query, year_from=year_from, mailto=OPENALEX_EMAIL)
        except Exception as e:  # noqa: BLE001
            log_event(run_dir, "retrieval", "query_failed", query=q, error=str(e))
            res = []
        log_event(run_dir, "retrieval", "query_done", query=q, hits=len(res))
        per_query_results.append(res)

    # round-robin so every query contributes to the final capped list
    raw = [r for group in zip_longest(*per_query_results) for r in group if r]
    deduped, removed = deduplicate(raw)
    capped = deduped[:MAX_RECORDS]
    for i, r in enumerate(capped, 1):
        r["uid"] = f"R{i:03d}"

    prisma = {
        "identified": len(raw),
        "duplicates_removed": removed,
        "after_dedup": len(deduped),
        "screened": len(capped),
    }
    (Path(run_dir) / "records.json").write_text(
        json.dumps(capped, indent=2, ensure_ascii=False), encoding="utf-8")
    log_event(run_dir, "retrieval", "done", **prisma)
    return {"raw_records": raw, "records": capped, "prisma": prisma}
