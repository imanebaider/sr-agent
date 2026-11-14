# sr-agent — Agentic AI Pipeline for Systematic Reviews (PRISMA 2020)

An autonomous multi-agent system (LangGraph + RAG + Claude) that takes a research question and
produces a transparent, reproducible systematic-review draft: protocol → search → deduplication →
dual screening → full-text/RAG extraction → risk-of-bias → synthesis → critic → report.

## Architecture

```
question ─► Planner ─► Search Strategy ─► Retrieval ─► Screening (A + B + arbiter)
                                                            │
                                                     Human gate (optional)
                                                            ▼
            report.md/.docx/.bib ◄── Critic ◄── Synthesis ◄── Extraction & Appraisal (RAG)
                                        └──(revise, max 1×)──┘
```

| Agent | File | Role |
|---|---|---|
| Planner | `src/agents/planner.py` | Question → protocol (PICO, criteria) or load a YAML template |
| Search Strategy | `src/agents/search_strategy.py` | Boolean queries + MeSH terms |
| Retrieval | `src/agents/retrieval.py` | OpenAlex search, dedup (DOI + fuzzy title), PRISMA counts |
| Screening | `src/agents/screening.py` | 2 independent reviewers (strict / inclusive) + arbiter, Cohen's kappa |
| Extraction & Appraisal | `src/agents/extraction.py` | OA PDF → hybrid RAG (Chroma dense + BM25, RRF fusion) → structured extraction + risk of bias + full-text eligibility |
| Synthesis & Writing | `src/agents/synthesis.py` | Evidence table, narrative, PRISMA data, BibTeX, DOCX |
| Critic / QA | `src/agents/critic.py` | Checks claims vs evidence and protocol; can send the report back once |

Transparency: every LLM call (prompt, model, temperature, output) and every decision is appended to
`audit.jsonl`; every screening/extraction decision stores a rationale; evidence quotes are checked
verbatim against the source text (`quote_verified`).

## Installation

```bash
git clone <your-repo-url> && cd sr-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit: ANTHROPIC_API_KEY, OPENALEX_EMAIL
```

`.env` settings: `ANTHROPIC_API_KEY`, `MODEL_NAME`, `OPENALEX_EMAIL` (polite pool),
`SCREEN_WORKERS` (parallel LLM calls), `MAX_RECORDS` (records screened), `MAX_INCLUDED` (cap for extraction).

## Usage

```bash
# 1) any topic, fully automatic
python main.py --question "Does physical exercise reduce depressive symptoms in adults?"

# 2) with your own PRISMA protocol template + human approval after screening
python main.py --question "Exercise and depression" \
               --protocol config/protocol_template.yaml --human-review

# 3) evaluate a finished run
python eval/evaluate.py outputs/run_YYYYMMDD_HHMMSS --manual-hours 60
#    optional recall check: --gold eval/gold_dois.txt

# 4) unit tests (no API key needed)
pytest -q
```

Outputs in `outputs/run_*/`: `protocol.yaml`, `search_strategy.json`, `records.json`,
`screening.json`, `extraction.json`, `evidence_table.csv`, `prisma_flow.json`, `report.md`,
`report.docx`, `references.bib`, `critic.json`, `metrics.json`, `audit.jsonl`, `eval_report.md`.

## Limitations

- Sources: OpenAlex only (extend `src/tools/` for PubMed / Semantic Scholar / arXiv).
- Full text only for open-access PDFs; otherwise extraction falls back to the abstract (flagged in the evidence table).
- No meta-analysis computation (narrative synthesis; evidence table is ready for it).
- The output is a **draft** — a human must verify it before any use.

## Development steps followed

1. Repo + environment + OpenAlex retrieval test.
2. Pydantic schemas and LangGraph state.
3. Planner / Search / Retrieval / dedup.
4. Dual screening with rationales + kappa.
5. Hybrid RAG + extraction + risk of bias.
6. Synthesis, exports, critic loop, human gate.
7. Evaluation script, tests, demo run.

## Demo (≤ 3 min video)

Show: command → protocol/queries → PRISMA counts → screening reasons (`screening.json`) →
`report.md` → `eval_report.md`. Mention human-in-the-loop and the audit log.

## Team contributions

| Member | Contribution |
|---|---|
| _Name 1_ | _e.g. Planner, Search Strategy, protocol template_ |
| _Name 2_ | _e.g. Retrieval, dedup, OpenAlex tool_ |
| _Name 3_ | _e.g. Screening, kappa, evaluation_ |
| _Name 4_ | _e.g. RAG, extraction_ |
| _Name 5_ | _e.g. Synthesis, critic, README, video_ |
