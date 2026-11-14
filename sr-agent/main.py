"""CLI entry point.

Examples:
  python main.py --question "Does physical exercise reduce depressive symptoms in adults?"
  python main.py --protocol config/protocol_template.yaml --question "Exercise and depression" --human-review
"""
import argparse
import json
import time
from pathlib import Path

from src.audit import log_event
from src.graph import build_graph


def main() -> None:
    ap = argparse.ArgumentParser(description="Agentic systematic-review pipeline (PRISMA 2020)")
    ap.add_argument("--question", required=True, help="Research question / topic")
    ap.add_argument("--protocol", default=None, help="Optional YAML protocol (skips the Planner LLM)")
    ap.add_argument("--human-review", action="store_true", help="Pause for approval after screening")
    ap.add_argument("--out", default="outputs", help="Output base directory")
    args = ap.parse_args()

    run_dir = Path(args.out) / time.strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_config.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")
    log_event(str(run_dir), "main", "start", question=args.question)

    t0 = time.time()
    final = build_graph().invoke(
        {"question": args.question, "protocol_path": args.protocol, "run_dir": str(run_dir),
         "ask_human": args.human_review, "revisions": 0, "metrics": {}},
        {"recursion_limit": 50})
    elapsed = time.time() - t0

    metrics = dict(final.get("metrics", {}))
    metrics["elapsed_seconds"] = round(elapsed, 1)
    metrics["prisma"] = final.get("prisma", {})
    metrics["critic_score"] = (final.get("critic") or {}).get("score")
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    log_event(str(run_dir), "main", "end", elapsed=elapsed)

    print(f"\nDone in {elapsed/60:.1f} min -> {run_dir}")
    print("PRISMA:", json.dumps(metrics["prisma"]))
    if (run_dir / "report.md").exists():
        print(f"Report: {run_dir / 'report.md'}")


if __name__ == "__main__":
    main()
