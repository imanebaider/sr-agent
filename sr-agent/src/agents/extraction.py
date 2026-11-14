"""Extraction & Appraisal agent: full-text fetch -> RAG index -> structured extraction + RoB."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.audit import log_event
from src.config import SCREEN_WORKERS
from src.llm import ask_structured
from src.metrics import quote_in_context
from src.rag.indexer import HybridIndex
from src.schemas import Extraction
from src.state import State
from src.tools.pdf_parser import fetch_fulltext

SYSTEM = (
    "You extract data from a study for a systematic review and appraise its risk of bias "
    "(Cochrane RoB-style: randomisation, blinding, attrition, selective reporting). "
    "Use ONLY the provided context. If something is not in the context write 'not reported' - "
    "never guess. Also confirm eligibility against the protocol (full-text eligibility check). "
    "If only an abstract is available, be lenient on eligibility and use risk_of_bias='unclear' "
    "unless the abstract states otherwise. evidence_quote must be a short VERBATIM excerpt."
)
QUERIES = [
    "study design randomized participants sample size",
    "intervention and comparator control group",
    "primary outcome results effect size mean difference",
    "limitations randomization blinding dropout attrition bias",
]


def extraction_node(state: State) -> dict:
    run_dir, proto, included = state["run_dir"], state["protocol"], state["included"]

    with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as ex:
        texts = list(ex.map(lambda r: fetch_fulltext(r["oa_pdf_url"]) if r.get("oa_pdf_url") else None,
                            included))

    index = HybridIndex(Path(run_dir) / "chroma")
    sources = {}
    for r, txt in zip(included, texts):
        if txt:
            index.add(r["uid"], f"{r['title']}\n\n{txt}", "fulltext")
            sources[r["uid"]] = "fulltext"
        else:
            index.add(r["uid"], f"{r['title']}\n\n{r['abstract']}", "abstract")
            sources[r["uid"]] = "abstract"
    n_fulltext = sum(v == "fulltext" for v in sources.values())

    def extract(r: dict) -> dict:
        seen, parts = set(), []
        for q in QUERIES:
            for c in index.search(q, doc_id=r["uid"], k=3):
                if c not in seen:
                    seen.add(c)
                    parts.append(c)
        context = "\n---\n".join(parts)[:12000]
        user = (f"Protocol criteria:\nInclusion: {proto['inclusion_criteria']}\n"
                f"Exclusion: {proto['exclusion_criteria']}\n\n"
                f"Text source: {sources[r['uid']]}\nStudy: {r['title']} ({r.get('year')})\n\n"
                f"Context:\n{context}")
        try:
            ext = ask_structured(Extraction, SYSTEM, user, run_dir=run_dir,
                                 agent="extraction", tag=r["uid"])
            d = ext.model_dump()
        except Exception as e:  # noqa: BLE001
            d = Extraction(eligible=False, ineligible_reason=f"extraction error: {e}").model_dump()
        d.update({"uid": r["uid"], "title": r["title"], "year": r.get("year"),
                  "doi": r.get("doi"), "authors": r.get("authors", []),
                  "journal": r.get("journal", ""), "text_source": sources[r["uid"]],
                  "quote_verified": quote_in_context(d.get("evidence_quote", ""), context)})
        return d

    with ThreadPoolExecutor(max_workers=SCREEN_WORKERS) as ex:
        extractions = list(ex.map(extract, included))

    eligible = [e for e in extractions if e["eligible"]]
    prisma = dict(state["prisma"])
    prisma["fulltext_retrieved"] = n_fulltext
    prisma["excluded_fulltext_stage"] = len(extractions) - len(eligible)
    prisma["included_final"] = len(eligible)

    (Path(run_dir) / "extraction.json").write_text(
        json.dumps(extractions, indent=2, ensure_ascii=False), encoding="utf-8")
    log_event(run_dir, "extraction", "done", **prisma)
    return {"extractions": extractions, "prisma": prisma}
