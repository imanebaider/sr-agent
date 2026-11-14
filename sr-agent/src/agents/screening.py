"""Dual screening agents (title/abstract) + arbiter, with Cohen's kappa."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.audit import log_event
from src.config import MAX_INCLUDED, SCREEN_WORKERS
from src.llm import ask_structured
from src.metrics import cohen_kappa
from src.schemas import ScreenDecision
from src.state import State

BASE = (
    "You screen titles and abstracts for a systematic review. Decide strictly from the "
    "protocol's criteria. Give a short reason that cites the specific criterion."
)
REVIEWERS = {
    "A": BASE + " You are a STRICT reviewer: include only if the abstract clearly satisfies "
                "all inclusion criteria and no exclusion criterion.",
    "B": BASE + " You are an INCLUSIVE reviewer: include if the abstract plausibly satisfies "
                "the criteria; exclude only when it clearly violates them.",
}
ARBITER = (
    BASE + " You are a senior reviewer resolving a disagreement between two reviewers. "
           "Weigh their reasons against the protocol and make the final call."
)


def _fmt_protocol(p: dict) -> str:
    return (
        f"Objective: {p['objective']}\nPICO: {json.dumps(p['pico'])}\n"
        "Inclusion:\n" + "\n".join(f"- {c}" for c in p["inclusion_criteria"]) +
        "\nExclusion:\n" + "\n".join(f"- {c}" for c in p["exclusion_criteria"])
    )


def _fmt_record(r: dict) -> str:
    return f"Title: {r['title']}\nYear: {r.get('year')}\nAbstract: {r['abstract'][:2500]}"


def _safe(fn, *a, **kw) -> ScreenDecision:
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        return ScreenDecision(decision="include", confidence=0.0,
                              reason=f"[LLM error - kept for human check] {e}")


def screening_node(state: State) -> dict:
    run_dir, proto, records = state["run_dir"], state["protocol"], state["records"]
    ptxt = _fmt_protocol(proto)

    def review(who: str, r: dict) -> ScreenDecision:
        return ask_structured(ScreenDecision, REVIEWERS[who], f"{ptxt}\n\n{_fmt_record(r)}",
                              run_dir=run_dir, agent=f"screening_{who}", tag=r["uid"])

    def run_reviewer(who: str) -> list[ScreenDecision]:
        with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as ex:
            return list(ex.map(lambda r: _safe(review, who, r), records))

    dec_a, dec_b = run_reviewer("A"), run_reviewer("B")

    def arbitrate(args) -> ScreenDecision:
        r, a, b = args
        user = (f"{ptxt}\n\n{_fmt_record(r)}\n\nReviewer A: {a.decision} - {a.reason}\n"
                f"Reviewer B: {b.decision} - {b.reason}")
        return ask_structured(ScreenDecision, ARBITER, user, run_dir=run_dir,
                              agent="screening_arbiter", tag=r["uid"])

    final: list[dict] = []
    disagree = [(r, a, b) for r, a, b in zip(records, dec_a, dec_b) if a.decision != b.decision]
    with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as ex:
        arb = dict(zip([r["uid"] for r, _, _ in disagree],
                       ex.map(lambda t: _safe(arbitrate, t), disagree)))

    for r, a, b in zip(records, dec_a, dec_b):
        if a.decision == b.decision:
            d, how = a, "agreement"
        else:
            d, how = arb[r["uid"]], "arbiter"
        final.append({
            "uid": r["uid"], "title": r["title"], "decision": d.decision, "reason": d.reason,
            "confidence": d.confidence, "resolved_by": how,
            "reviewer_A": {"decision": a.decision, "reason": a.reason},
            "reviewer_B": {"decision": b.decision, "reason": b.reason},
        })

    kappa = cohen_kappa([a.decision for a in dec_a], [b.decision for b in dec_b])
    agree = sum(a.decision == b.decision for a, b in zip(dec_a, dec_b)) / max(1, len(records))
    included_uids = [f["uid"] for f in final if f["decision"] == "include"][:MAX_INCLUDED]
    included = [r for r in records if r["uid"] in set(included_uids)]

    prisma = dict(state["prisma"])
    prisma["excluded_title_abstract"] = len(records) - len(
        [f for f in final if f["decision"] == "include"])
    prisma["sought_for_fulltext"] = len(included)

    (Path(run_dir) / "screening.json").write_text(
        json.dumps(final, indent=2, ensure_ascii=False), encoding="utf-8")
    metrics = dict(state.get("metrics", {}))
    metrics.update({"kappa": kappa, "raw_agreement": agree, "n_screened": len(records),
                    "n_disagreements": len(disagree), "n_included_screening": len(included)})
    log_event(run_dir, "screening", "done", kappa=kappa, agreement=agree, included=len(included))
    return {"screening": final, "included": included, "prisma": prisma, "metrics": metrics}
