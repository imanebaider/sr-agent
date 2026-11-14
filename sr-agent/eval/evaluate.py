"""Evaluation of a finished run.

Usage:
  python eval/evaluate.py outputs/run_XXXX [--gold eval/gold_dois.txt] [--manual-hours 60]

Measures: completeness, accuracy (recall vs gold standard + quote grounding), inter-agent
consistency (Cohen's kappa), traceability and time savings vs a manual review.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.tools.dedup import norm_doi  # noqa: E402

KEY_FIELDS = ["study_design", "population", "sample_size", "intervention", "comparator",
              "outcomes", "main_findings"]


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--gold", help="text file with one DOI per line (known relevant studies)")
    ap.add_argument("--manual-hours", type=float, default=60.0,
                    help="assumed hours of a manual review (default 60)")
    a = ap.parse_args()
    run = Path(a.run_dir)

    metrics = load(run / "metrics.json") or {}
    screening = load(run / "screening.json") or []
    extraction = load(run / "extraction.json") or []
    records = load(run / "records.json") or []
    critic = load(run / "critic.json") or {}

    lines = [f"# Evaluation report - {run.name}\n"]

    # completeness
    eligible = [e for e in extraction if e["eligible"]]
    filled = [sum(e[f] != "not reported" for f in KEY_FIELDS) / len(KEY_FIELDS) for e in eligible]
    completeness = sum(filled) / len(filled) if filled else 0.0
    ft = sum(e["text_source"] == "fulltext" for e in eligible)
    lines.append(f"- Extraction completeness (key fields filled): **{completeness:.0%}** "
                 f"({len(eligible)} studies, {ft} with full text)")

    # grounding
    verified = [e["quote_verified"] for e in eligible]
    if verified:
        lines.append(f"- Evidence quotes found verbatim in source text: **{sum(verified)/len(verified):.0%}**")

    # traceability
    traced = [bool(s.get("reason", "").strip()) for s in screening]
    if traced:
        lines.append(f"- Screening decisions with a rationale: **{sum(traced)/len(traced):.0%}**")

    # inter-agent consistency
    lines.append(f"- Reviewer A/B agreement: **{metrics.get('raw_agreement', float('nan')):.0%}**, "
                 f"Cohen's kappa = **{metrics.get('kappa', float('nan')):.2f}** "
                 f"({metrics.get('n_disagreements', 0)} disagreements resolved by arbiter)")

    # recall vs gold standard
    if a.gold:
        gold = {norm_doi(l) for l in Path(a.gold).read_text().splitlines() if l.strip()}
        retrieved = {norm_doi(r.get("doi")) for r in records}
        inc_uids = {s["uid"] for s in screening if s["decision"] == "include"}
        incl = {norm_doi(r.get("doi")) for r in records if r["uid"] in inc_uids}
        lines.append(f"- Gold standard ({len(gold)} DOIs): search recall = "
                     f"**{len(gold & retrieved)/max(1, len(gold)):.0%}**, screening recall = "
                     f"**{len(gold & incl)/max(1, len(gold & retrieved)):.0%}** (of retrieved gold)")

    # time savings
    el = metrics.get("elapsed_seconds")
    if el:
        manual = a.manual_hours * 3600
        lines.append(f"- Pipeline time: **{el/60:.1f} min** vs assumed manual {a.manual_hours:.0f} h "
                     f"-> time saved ~ **{1 - el/manual:.1%}**")

    if critic:
        lines.append(f"- Critic agent score: **{critic.get('score')}/10** (verdict: {critic.get('verdict')})")
    lines.append(f"- PRISMA numbers: `{json.dumps(metrics.get('prisma', {}))}`")

    report = "\n".join(lines) + "\n"
    (run / "eval_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
