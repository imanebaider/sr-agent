"""Audit trail: every agent decision / LLM call is appended to audit.jsonl."""
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def log_event(run_dir: str, agent: str, event: str, **data) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": agent, "event": event, **data}
    with _lock:
        with open(Path(run_dir) / "audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
