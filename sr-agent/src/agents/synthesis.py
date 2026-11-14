"""Synthesis & Writing agent: evidence table + narrative + references + exports."""
import csv
import json
import re
from pathlib import Path

from src.audit import log_event
from src.llm import ask_text
from src.state import State

SYSTEM = (
    "You are a scientific writer drafting a systematic review following PRISMA 2020. "
    "Write ONLY these sections in Markdown: Abstract, Introduction, Methods, Results "
    "(study characteristics, narrative synthesis of findings, risk of bias), Discussion, "
    "Limitations, Conclusion. Rules: (1) use ONLY facts in the evidence table and the facts I "
    "give you; (2) cite studies as [n] using the table numbers; (3) do not invent numbers; "
    "(4) say plainly when evidence is thin or only abstracts were available; (5) do NOT write "
    "the references list, the PRISMA numbers table or the evidence table - they are appended "
    "automatically."
)


def _cell(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


def _first_author(e: dict) -> str:
    a = e.get("authors") or []
    return (a[0].split()[-1] if a else "Anon") + (" et al." if len(a) > 1 else "")


def evidence_table_md(rows: list[dict]) -> str:
    head = "| # | Study | Design | N | Population | Intervention | Comparator | Key findings | RoB | Text |\n"
    head += "|---|---|---|---|---|---|---|---|---|---|\n"
    body = ""
    for n, e in enumerate(rows, 1):
        body += (f"| {n} | {_first_author(e)} ({e.get('year')}) | {_cell(e['study_design'])} | "
                 f"{_cell(e['sample_size'])} | {_cell(e['population'])} | {_cell(e['intervention'])} | "
                 f"{_cell(e['comparator'])} | {_cell(e['main_findings'])} | {e['risk_of_bias']} | "
                 f"{e['text_source']} |\n")
    return head + body


def bibtex(rows: list[dict]) -> str:
    out = []
    for n, e in enumerate(rows, 1):
        last = re.sub(r"\W", "", (e["authors"][0].split()[-1] if e.get("authors") else "anon"))
        key = f"{last.lower()}{e.get('year')}_{n}"
        doi = (e.get("doi") or "").replace("https://doi.org/", "")
        out.append(
            f"@article{{{key},\n  title = {{{e['title']}}},\n"
            f"  author = {{{' and '.join(e.get('authors') or ['Anonymous'])}}},\n"
            f"  year = {{{e.get('year')}}},\n  journal = {{{e.get('journal', '')}}},\n"
            f"  doi = {{{doi}}}\n}}\n")
    return "\n".join(out)


def references_md(rows: list[dict]) -> str:
    lines = []
    for n, e in enumerate(rows, 1):
        authors = ", ".join(e.get("authors") or ["Anonymous"])
        lines.append(f"{n}. {authors} ({e.get('year')}). {e['title']}. {e.get('journal', '')}. "
                     f"{e.get('doi') or ''}")
    return "\n".join(lines)


def prisma_md(p: dict) -> str:
    return (
        "| Stage | n |\n|---|---|\n"
        f"| Records identified (OpenAlex) | {p.get('identified', 0)} |\n"
        f"| Duplicates removed | {p.get('duplicates_removed', 0)} |\n"
        f"| Records screened (title/abstract) | {p.get('screened', 0)} |\n"
        f"| Excluded at title/abstract | {p.get('excluded_title_abstract', 0)} |\n"
        f"| Sought for full-text/appraisal | {p.get('sought_for_fulltext', 0)} |\n"
        f"| Full texts retrieved (others: abstract only) | {p.get('fulltext_retrieved', 0)} |\n"
        f"| Excluded at full-text/appraisal | {p.get('excluded_fulltext_stage', 0)} |\n"
        f"| **Studies included in synthesis** | **{p.get('included_final', 0)}** |\n")


def md_to_docx(md: str, path: Path) -> None:
    try:
        from docx import Document
    except ImportError:
        return
    doc = Document()
    table_rows: list[list[str]] = []

    def flush():
        if not table_rows:
            return
        t = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        t.style = "Table Grid"
        for i, row in enumerate(table_rows):
            for j, val in enumerate(row[:len(table_rows[0])]):
                t.cell(i, j).text = val
        table_rows.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                table_rows.append(cells)
            continue
        flush()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            doc.add_heading(line.lstrip("# ").strip(), level=min(level, 3))
        elif line.strip():
            doc.add_paragraph(line.replace("**", ""))
    flush()
    doc.save(str(path))


def synthesis_node(state: State) -> dict:
    run_dir = state["run_dir"]
    rows = [e for e in state.get("extractions", []) if e["eligible"]]
    p = state["prisma"]
    proto = state["protocol"]

    facts = (
        f"Title: {proto['title']}\nObjective: {proto['objective']}\nPICO: {proto['pico']}\n"
        f"Inclusion: {proto['inclusion_criteria']}\nExclusion: {proto['exclusion_criteria']}\n"
        f"Search queries (OpenAlex): {state['strategy']['queries']}\n"
        f"Screening: two independent LLM reviewers + arbiter; Cohen's kappa = "
        f"{state.get('metrics', {}).get('kappa')}\nPRISMA numbers: {p}\n"
        "Note: this review was drafted by an AI pipeline and requires human verification."
    )
    table = evidence_table_md(rows)
    user = f"{facts}\n\nEVIDENCE TABLE:\n{table}"
    if state.get("revisions", 0) > 0 and state.get("critic"):
        c = state["critic"]
        user += ("\n\nThe quality-assurance critic rejected the previous draft. Fix these:\n"
                 f"Issues: {c['issues']}\nUnsupported claims: {c['unsupported_claims']}")
    if not rows:
        user += "\n\nNo study met the criteria: write a short report stating this honestly."

    body = ask_text(SYSTEM, user, run_dir=run_dir, agent="synthesis", tag="report")

    full = (f"# {proto['title']}\n\n> AI-generated draft (PRISMA 2020). Human verification required.\n\n"
            f"{body}\n\n## PRISMA flow data\n\n{prisma_md(p)}\n## Evidence table\n\n{table}\n"
            f"## References\n\n{references_md(rows)}\n")
    out = Path(run_dir)
    (out / "report.md").write_text(full, encoding="utf-8")
    (out / "references.bib").write_text(bibtex(rows), encoding="utf-8")
    (out / "prisma_flow.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    with open(out / "evidence_table.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["uid", "title", "year", "doi", "study_design", "sample_size", "population",
                "intervention", "comparator", "outcomes", "main_findings", "risk_of_bias",
                "rob_rationale", "text_source", "quote_verified"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    md_to_docx(full, out / "report.docx")
    log_event(run_dir, "synthesis", "report_written", studies=len(rows),
              revision=state.get("revisions", 0))
    return {"report_body": full}
