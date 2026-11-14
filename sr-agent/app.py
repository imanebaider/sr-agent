
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sr-agent - Lite UI
Lancement:
    python app_lite.py

Puis ouvrir:
    http://127.0.0.1:8501
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import traceback
import webbrowser

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, quote, urlparse


# ============================================================
# CONFIGURATION
# ============================================================

BASE = Path(__file__).resolve().parent
OUT = BASE / "outputs"

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8501"))

LOG_LIMIT = 30000

ALLOWED_HOSTS = {
    f"127.0.0.1:{PORT}",
    f"localhost:{PORT}",
}

TABS = (
    "Report",
    "PRISMA",
    "Screening",
    "Evidence table",
    "Critic",
    "Audit",
    "Downloads",
)

DOWNLOADS = (
    "report.docx",
    "report.md",
    "references.bib",
    "evidence_table.csv",
    "protocol.yaml",
    "eval_report.md",
    "metrics.json",
    "extraction.json",
    "screening.json",
    "critic.json",
    "audit.jsonl",
    "records.json",
    "prisma_flow.json",
)

PRISMA_STEPS = (
    ("Records identified", "identified"),
    ("Duplicates removed", "duplicates_removed"),
    ("After deduplication", "after_dedup"),
    ("Screened (title/abstract)", "screened"),
    ("Excluded (title/abstract)", "excluded_title_abstract"),
    ("Sought for full text", "sought_for_fulltext"),
    ("Full text retrieved", "fulltext_retrieved"),
    ("Excluded (full text)", "excluded_fulltext_stage"),
    ("Included in review", "included_final"),
)

log = logging.getLogger("sr-agent-ui")


# ============================================================
# RUN DISCOVERY
# ============================================================

def list_runs() -> list[str]:
    """
    Retourne les exécutions, de la plus récente à la plus ancienne.
    Les noms run_YYYYMMDD_HHMMSS sont triés chronologiquement.
    """
    if not OUT.exists():
        return []

    runs = [
        p.name
        for p in OUT.iterdir()
        if p.is_dir() and p.name.startswith("run_")
    ]

    return sorted(runs, reverse=True)


def latest_run() -> str | None:
    runs = list_runs()
    return runs[0] if runs else None


def get_run_dir(run: str) -> Path | None:
    """
    Vérifie que le dossier demandé appartient bien à outputs.
    """
    if not run or run not in list_runs():
        return None

    candidate = (OUT / run).resolve()

    try:
        candidate.relative_to(OUT.resolve())
    except ValueError:
        return None

    if not candidate.is_dir():
        return None

    return candidate


def find_artifact(directory: Path, filename: str) -> Path | None:
    """
    Cherche un fichier dans le dossier du run et ses sous-dossiers.
    La recherche du nom est insensible à la casse.
    """
    if not directory.exists():
        return None

    direct = directory / filename

    if direct.is_file():
        return direct

    wanted = filename.lower()

    try:
        for path in directory.rglob("*"):
            if path.is_file() and path.name.lower() == wanted:
                return path
    except OSError:
        return None

    return None


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


# ============================================================
# PIPELINE RUNNER
# ============================================================

@dataclass
class RunState:
    proc: subprocess.Popen | None = None
    log: str = ""
    running: bool = False
    returncode: int | None = None


class PipelineRunner:
    """
    Lance main.py dans un processus indépendant.
    """

    def __init__(self) -> None:
        self._state = RunState()
        self._lock = threading.Lock()

    def start(self, question: str, protocol: str | None) -> bool:

        question = question.strip()

        if not question:
            return False

        cmd = [
            sys.executable,
            "-u",
            "main.py",
            "--question",
            question,
        ]

        if protocol:
            cmd.extend(["--protocol", protocol])

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        with self._lock:

            if self._state.running:
                return False

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(BASE),
                    env=env,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )

            except Exception:
                log.exception("Impossible de lancer le pipeline")
                return False

            self._state = RunState(
                proc=proc,
                log="$ " + subprocess.list2cmdline(cmd) + "\n",
                running=True,
                returncode=None,
            )

        threading.Thread(
            target=self._pump,
            args=(proc,),
            daemon=True,
        ).start()

        return True

    def _pump(self, proc: subprocess.Popen) -> None:

        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    with self._lock:
                        self._state.log = (
                            self._state.log + line
                        )[-LOG_LIMIT:]

            proc.wait()

        except Exception:
            log.exception("Erreur pendant la lecture du processus")

        finally:
            with self._lock:
                self._state.running = False
                self._state.returncode = proc.returncode

                self._state.log = (
                    self._state.log
                    + f"\nProcess finished with code {proc.returncode}\n"
                )[-LOG_LIMIT:]

    def stop(self) -> None:

        with self._lock:
            proc = (
                self._state.proc
                if self._state.running
                else None
            )

        if proc:
            try:
                proc.terminate()
            except OSError:
                log.exception("Impossible d'arrêter le processus")

    def snapshot(self) -> dict[str, Any]:

        with self._lock:
            state = self._state

            result = {
                "running": state.running,
                "returncode": state.returncode,
                "log": state.log,
            }

        result["latest_run"] = latest_run()

        return result


RUNNER = PipelineRunner()


# ============================================================
# DATA ACCESS
# ============================================================

def read_json(path: Path) -> Any:

    try:
        return json.loads(read_text_file(path))

    except (OSError, ValueError, TypeError):
        return None


def read_run_json(directory: Path, filename: str) -> Any:

    path = find_artifact(directory, filename)

    if path is None:
        return None

    return read_json(path)


def safe_protocol(value: str) -> str | None:
    """
    Accepte uniquement les fichiers YAML situés dans le projet.
    """

    if not value:
        return None

    try:
        candidate = (BASE / value).resolve()

        candidate.relative_to(BASE.resolve())

        if candidate.is_file():
            return str(candidate.relative_to(BASE.resolve()))

    except (ValueError, OSError):
        return None

    return None


def flatten(data: dict, prefix: str = "") -> dict[str, Any]:

    output: dict[str, Any] = {}

    for key, value in data.items():

        name = f"{prefix}.{key}" if prefix else str(key)

        if isinstance(value, dict):
            output.update(flatten(value, name))

        elif isinstance(value, list):
            output[name] = json.dumps(
                value,
                ensure_ascii=False,
            )

        else:
            output[name] = value

    return output


def to_rows(data: Any) -> list[dict[str, Any]]:

    if isinstance(data, dict):

        if isinstance(data.get("records"), list):
            data = data["records"]

        elif data and all(
            isinstance(value, dict)
            for value in data.values()
        ):
            return [
                {"id": key, **flatten(value)}
                for key, value in data.items()
            ]

        else:
            return [flatten(data)]

    if isinstance(data, list):

        return [
            flatten(item)
            if isinstance(item, dict)
            else {"value": item}
            for item in data
        ]

    if data is None:
        return []

    return [{"value": data}]


# ============================================================
# HTML HELPERS
# ============================================================

esc = html.escape


def muted(message: str) -> str:
    return f"<div class='empty'>{esc(message)}</div>"


def clip(value: Any, limit: int = 600) -> str:

    value = "" if value is None else str(value)

    if len(value) > limit:
        return value[:limit] + "…"

    return value


def grid(
    headers: Iterable[str],
    rows: Iterable[Iterable[Any]],
    fmt: Callable[[str], str] = esc,
) -> str:

    def format_cell(value: Any) -> str:
        return fmt("" if value is None else str(value))

    head = "".join(
        f"<th>{format_cell(cell)}</th>"
        for cell in headers
    )

    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{format_cell(cell)}</td>"
            for cell in row
        )
        + "</tr>"
        for row in rows
    )

    return (
        "<div class='tw'>"
        "<table>"
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
        "</div>"
    )


def rows_table(
    rows: list[dict[str, Any]],
    limit: int = 1000,
) -> str:

    if not rows:
        return muted("Aucune donnée disponible.")

    columns: list[str] = []

    for row in rows[:limit]:
        for key in row:
            if key not in columns:
                columns.append(key)

    body = [
        [row.get(column, "") for column in columns]
        for row in rows[:limit]
    ]

    note = ""

    if len(rows) > limit:
        note = (
            f"<p class='note'>Affichage des {limit} "
            f"premières lignes sur {len(rows)}.</p>"
        )

    return note + grid(
        columns,
        body,
        fmt=lambda value: esc(clip(value)),
    )


# ============================================================
# MARKDOWN RENDERER
# ============================================================

def inline(text: str) -> str:

    text = esc(text)

    text = re.sub(
        r"`([^`]+)`",
        r"<code>\1</code>",
        text,
    )

    text = re.sub(
        r"\*\*([^*]+)\*\*",
        r"<b>\1</b>",
        text,
    )

    text = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        r"<i>\1</i>",
        text,
    )

    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        text,
    )

    return text


def md_to_html(text: str) -> str:

    output: list[str] = []
    paragraph: list[str] = []
    current_list: str | None = None

    lines = text.splitlines()

    def flush_paragraph() -> None:

        if paragraph:
            output.append(
                f"<p>{inline(' '.join(paragraph))}</p>"
            )
            paragraph.clear()

    def close_list() -> None:

        nonlocal current_list

        if current_list:
            output.append(f"</{current_list}>")
            current_list = None

    def cells(line: str) -> list[str]:
        return [
            cell.strip()
            for cell in line.strip().strip("|").split("|")
        ]

    index = 0

    while index < len(lines):

        line = lines[index].strip()

        if line.startswith("```"):

            flush_paragraph()
            close_list()

            code_lines: list[str] = []

            index += 1

            while (
                index < len(lines)
                and not lines[index].strip().startswith("```")
            ):
                code_lines.append(lines[index])
                index += 1

            output.append(
                f"<pre>{esc(chr(10).join(code_lines))}</pre>"
            )

            index += 1
            continue

        if (
            line.startswith("|")
            and index + 1 < len(lines)
            and re.match(
                r"^\s*\|?\s*:?-{2,}",
                lines[index + 1],
            )
        ):

            flush_paragraph()
            close_list()

            headers = cells(line)

            index += 2

            table_rows = []

            while (
                index < len(lines)
                and lines[index].strip().startswith("|")
            ):
                table_rows.append(cells(lines[index]))
                index += 1

            output.append(
                grid(headers, table_rows, fmt=inline)
            )

            continue

        heading = re.match(r"^(#{1,6})\s+(.*)", line)

        if heading:

            flush_paragraph()
            close_list()

            level = len(heading.group(1))

            output.append(
                f"<h{level}>{inline(heading.group(2))}</h{level}>"
            )

        elif re.match(r"^(-{3,}|\*{3,})$", line):

            flush_paragraph()
            close_list()
            output.append("<hr>")

        elif item := re.match(
            r"^([-*+]|\d+\.)\s+(.*)",
            line,
        ):

            flush_paragraph()

            tag = (
                "ol"
                if item.group(1)[0].isdigit()
                else "ul"
            )

            if current_list != tag:
                close_list()
                output.append(f"<{tag}>")
                current_list = tag

            output.append(
                f"<li>{inline(item.group(2))}</li>"
            )

        elif line.startswith(">"):

            flush_paragraph()
            close_list()

            output.append(
                f"<blockquote>{inline(line.lstrip('> '))}</blockquote>"
            )

        elif not line:

            flush_paragraph()
            close_list()

        else:

            close_list()
            paragraph.append(line)

        index += 1

    flush_paragraph()
    close_list()

    return "\n".join(output)


# ============================================================
# TABS
# ============================================================

def tab_report(directory: Path) -> str:

    path = find_artifact(directory, "report.md")

    if path is None:
        return muted(
            "report.md introuvable dans cette exécution."
        )

    content = read_text_file(path)

    if not content.strip():
        return muted("Le rapport est vide.")

    return (
        "<article class='prose'>"
        + md_to_html(content)
        + "</article>"
    )


def tab_prisma(prisma: dict[str, Any]) -> str:

    if not prisma:
        return muted("Aucune donnée PRISMA disponible.")

    values = [
        (label, prisma.get(key))
        for label, key in PRISMA_STEPS
    ]

    numbers = [
        value
        for _, value in values
        if isinstance(value, (int, float))
    ]

    maximum = max(numbers) if numbers else 1

    if maximum <= 0:
        maximum = 1

    rows = ""

    for label, value in values:

        if isinstance(value, (int, float)):
            percentage = max(
                0,
                min(100, 100.0 * value / maximum),
            )
            display = esc(str(value))
        else:
            percentage = 0
            display = "–"

        rows += (
            "<tr>"
            f"<td>{esc(label)}</td>"
            f"<td class='num'>{display}</td>"
            "<td class='bar'>"
            f"<div style='width:{percentage:.1f}%'></div>"
            "</td>"
            "</tr>"
        )

    warning = ""

    screened = prisma.get("screened")
    after = prisma.get("after_dedup")

    if (
        isinstance(screened, (int, float))
        and isinstance(after, (int, float))
        and screened < after
    ):

        warning = (
            "<div class='warn'>"
            "<b>Attention.</b> "
            f"Seuls {screened} enregistrements sur {after} "
            "ont été criblés. Vérifiez la configuration "
            "MAX_RECORDS de votre projet."
            "</div>"
        )

    return (
        "<div class='tw'>"
        "<table class='flow'>"
        "<thead><tr><th>Étape</th><th>n</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
        "</div>"
        + warning
    )


def tab_screening(directory: Path) -> str:

    data = read_run_json(directory, "screening.json")

    if data is None:
        return muted("screening.json introuvable.")

    return rows_table(to_rows(data))


def tab_evidence(directory: Path) -> str:

    path = find_artifact(
        directory,
        "evidence_table.csv",
    )

    if path is None:
        return muted(
            "evidence_table.csv introuvable dans cette exécution."
        )

    try:

        with path.open(
            encoding="utf-8-sig",
            errors="replace",
            newline="",
        ) as file:

            reader = csv.DictReader(file)
            rows = list(reader)

        if not reader.fieldnames:
            return muted(
                "Le fichier evidence_table.csv ne contient pas d'en-têtes."
            )

        if not rows:
            return muted(
                "Le fichier evidence_table.csv est vide."
            )

        return (
            f"<p class='note'>{len(rows)} études / enregistrements "
            "dans le tableau d'évidence.</p>"
            + rows_table(rows)
        )

    except (OSError, csv.Error) as error:

        return muted(
            f"Erreur de lecture du fichier CSV : {error}"
        )


def tab_critic(directory: Path) -> str:

    critic = read_run_json(directory, "critic.json")

    if critic is None:
        return muted("critic.json introuvable.")

    header = ""

    if isinstance(critic, dict) and "score" in critic:

        header = (
            "<div class='card big'>"
            "<span>Score du critique</span>"
            f"<b>{esc(str(critic['score']))}</b>"
            "</div>"
        )

    return (
        header
        + "<pre>"
        + esc(
            json.dumps(
                critic,
                indent=2,
                ensure_ascii=False,
            )
        )
        + "</pre>"
    )


def tab_audit(directory: Path) -> str:

    path = find_artifact(directory, "audit.jsonl")

    if path is None:
        return muted("audit.jsonl introuvable.")

    rows = []

    for line in read_text_file(path).splitlines():

        try:
            item = json.loads(line)

        except ValueError:
            continue

        if isinstance(item, dict):
            rows.append(flatten(item))
        else:
            rows.append({"value": item})

    return rows_table(rows)


def tab_downloads(run: str, directory: Path) -> str:

    items = []

    for name in DOWNLOADS:

        if find_artifact(directory, name) is not None:
            items.append(name)

    if not items:
        return muted(
            "Aucun fichier téléchargeable pour cette exécution."
        )

    links = "".join(
        (
            f"<a class='dl' href='/download?"
            f"run={quote(run)}&name={quote(name)}'>"
            f"<span>⬇</span>{esc(name)}</a>"
        )
        for name in items
    )

    return f"<div class='dls'>{links}</div>"


# ============================================================
# CSS
# ============================================================

CSS = """
:root {
  --bg:#f6f7fb;
  --surface:#fff;
  --ink:#0f172a;
  --muted:#64748b;
  --line:#e2e8f0;
  --accent:#4f46e5;
  --accent-soft:#eef2ff;
  --warn-bg:#fffbeb;
  --warn:#d97706;
  --side:#0f172a;
  --side-ink:#e2e8f0;
  --code:#f1f5f9;
  --radius:12px;
  --shadow:0 1px 2px rgba(15,23,42,.05),0 8px 24px rgba(15,23,42,.06);
}

@media(prefers-color-scheme:dark) {
  :root {
    --bg:#0b1020;
    --surface:#121a2f;
    --ink:#e6eaf5;
    --muted:#94a3b8;
    --line:#243049;
    --accent:#818cf8;
    --accent-soft:#1e2547;
    --warn-bg:#2a2210;
    --code:#1a2340;
    --side:#070b17;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.35);
  }
}

* {
  box-sizing:border-box;
}

html {
  -webkit-font-smoothing:antialiased;
}

body {
  margin:0;
  display:flex;
  min-height:100vh;
  background:var(--bg);
  color:var(--ink);
  font:15px/1.6 'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
}

aside {
  width:310px;
  flex:none;
  position:sticky;
  top:0;
  height:100vh;
  overflow:auto;
  background:linear-gradient(180deg,var(--side),#1e1b4b);
  color:var(--side-ink);
  padding:28px 22px;
}

aside h2 {
  margin:0;
  font-size:20px;
}

aside .tag {
  margin:2px 0 22px;
  color:#94a3b8;
  font-size:12px;
  text-transform:uppercase;
  letter-spacing:.14em;
}

aside label {
  display:block;
  margin:16px 0 6px;
  font-size:12px;
  font-weight:600;
  text-transform:uppercase;
  letter-spacing:.08em;
  color:#a5b4fc;
}

aside textarea,
aside input[type=text],
aside select {
  width:100%;
  padding:10px 12px;
  border-radius:10px;
  border:1px solid rgba(255,255,255,.14);
  background:rgba(255,255,255,.06);
  color:#fff;
  font:inherit;
  outline:none;
}

aside select option {
  color:#0f172a;
}

.chk {
  display:flex!important;
  align-items:center;
  gap:8px;
  text-transform:none!important;
  letter-spacing:0!important;
  font-weight:400!important;
  color:var(--side-ink)!important;
}

button {
  font:inherit;
  cursor:pointer;
  border-radius:10px;
  border:1px solid var(--line);
  background:var(--surface);
  color:var(--ink);
  padding:9px 14px;
}

button.primary {
  width:100%;
  margin-top:20px;
  padding:12px;
  border:0;
  font-weight:600;
  color:#fff;
  background:linear-gradient(135deg,#6366f1,#4f46e5);
}

button:disabled {
  opacity:.45;
  cursor:not-allowed;
}

main {
  flex:1;
  min-width:0;
  padding:36px 44px;
  max-width:1500px;
}

h1 {
  margin:0 0 4px;
  font-size:28px;
}

.sub {
  margin:0 0 24px;
  color:var(--muted);
  font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:13px;
}

.cards {
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:16px;
  margin-bottom:28px;
}

.card {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:var(--radius);
  padding:16px 20px;
  box-shadow:var(--shadow);
}

.card span {
  display:block;
  font-size:12px;
  color:var(--muted);
  text-transform:uppercase;
  letter-spacing:.08em;
}

.card b {
  display:block;
  margin-top:4px;
  font-size:30px;
}

.card.big {
  display:inline-block;
  min-width:220px;
  margin-bottom:16px;
}

.tabs {
  display:flex;
  gap:6px;
  flex-wrap:wrap;
  padding:5px;
  margin-bottom:22px;
  width:fit-content;
  max-width:100%;
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:999px;
  box-shadow:var(--shadow);
}

.tab {
  padding:7px 16px;
  border-radius:999px;
  color:var(--muted);
  text-decoration:none;
  font-weight:500;
  font-size:14px;
}

.tab.on {
  background:var(--accent);
  color:#fff;
}

.panel {
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:var(--radius);
  padding:26px 30px;
  box-shadow:var(--shadow);
}

.prose {
  max-width:860px;
  font-family:Georgia,'Iowan Old Style',serif;
  font-size:16.5px;
  line-height:1.75;
}

.prose h1,
.prose h2,
.prose h3 {
  font-family:'Inter',system-ui,sans-serif;
  margin:1.6em 0 .5em;
}

.prose a {
  color:var(--accent);
}

.tw {
  overflow:auto;
  max-width:100%;
  border:1px solid var(--line);
  border-radius:10px;
}

table {
  border-collapse:collapse;
  width:100%;
  font-size:13px;
}

th,td {
  padding:9px 12px;
  text-align:left;
  vertical-align:top;
  border-bottom:1px solid var(--line);
  max-width:420px;
  word-break:break-word;
}

th {
  position:sticky;
  top:0;
  background:var(--accent-soft);
  color:var(--accent);
  font-size:11.5px;
  text-transform:uppercase;
}

tbody tr:hover {
  background:var(--accent-soft);
}

tbody tr:last-child td {
  border-bottom:0;
}

.num {
  text-align:right;
  font-variant-numeric:tabular-nums;
  font-weight:600;
}

.bar {
  width:45%;
}

.bar div {
  height:10px;
  border-radius:999px;
  background:linear-gradient(90deg,#6366f1,#22d3ee);
}

pre {
  background:var(--code);
  padding:14px 16px;
  border-radius:10px;
  overflow:auto;
  white-space:pre-wrap;
  word-break:break-word;
  font:12.5px/1.55 ui-monospace,Menlo,Consolas,monospace;
}

code {
  background:var(--code);
  padding:2px 6px;
  border-radius:5px;
}

blockquote {
  margin:1em 0;
  padding:2px 16px;
  border-left:3px solid var(--accent);
  color:var(--muted);
}

hr {
  border:0;
  border-top:1px solid var(--line);
  margin:2em 0;
}

.warn {
  margin-top:18px;
  padding:12px 16px;
  border-radius:10px;
  background:var(--warn-bg);
  border-left:4px solid var(--warn);
}

.empty {
  padding:40px;
  text-align:center;
  color:var(--muted);
  border:1px dashed var(--line);
  border-radius:10px;
}

.note {
  color:var(--muted);
  font-size:13px;
}

.dls {
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(240px,1fr));
  gap:12px;
}

.dl {
  display:flex;
  align-items:center;
  gap:12px;
  padding:14px 16px;
  border:1px solid var(--line);
  border-radius:10px;
  color:var(--ink);
  text-decoration:none;
  font-weight:500;
}

.dl:hover {
  border-color:var(--accent);
  background:var(--accent-soft);
}

.dl span {
  color:var(--accent);
}

.logp {
  margin-bottom:24px;
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:var(--radius);
  padding:12px 18px;
  box-shadow:var(--shadow);
}

.logp summary {
  cursor:pointer;
  font-weight:600;
}

.logp pre {
  max-height:320px;
  margin-bottom:8px;
}

.hero {
  padding:80px 20px;
  text-align:center;
  color:var(--muted);
}

.hero h1 {
  color:var(--ink);
}

@media(max-width:900px) {
  body {
    display:block;
  }

  aside {
    position:static;
    width:auto;
    height:auto;
  }

  main {
    padding:22px;
  }
}
"""


# ============================================================
# JAVASCRIPT
# ============================================================

POLL_JS = """
const logEl = document.getElementById('log');
const st = document.getElementById('st');

async function poll() {
    try {
        const response = await fetch('/status', {
            cache: 'no-store'
        });

        const s = await response.json();

        logEl.textContent = s.log || '';
        logEl.scrollTop = logEl.scrollHeight;

        if (s.running) {
            st.textContent =
                'Exécution en cours… (gardez cette page ouverte)';

            setTimeout(poll, 2000);
            return;
        }

        if (s.returncode === 0 && s.latest_run) {
            const current = new URL(window.location.href);
            const currentRun = current.searchParams.get('run');

            if (currentRun !== s.latest_run) {
                st.textContent = 'Terminé ✔ — ouverture des résultats...';

                window.location.replace(
                    '/?run=' +
                    encodeURIComponent(s.latest_run) +
                    '&tab=Report'
                );

                return;
            }

            st.textContent = 'Terminé ✔';
        }
        else if (s.returncode !== null) {
            st.textContent =
                'Échec (code ' + s.returncode + ')';
        }

    } catch (error) {
        setTimeout(poll, 3000);
    }
}

poll();
"""


# ============================================================
# SIDEBAR
# ============================================================

def sidebar(
    runs: list[str],
    selected_run: str | None,
    running: bool,
) -> str:

    content = (
        "<h2>📚 sr-agent</h2>"
        "<div class='tag'>Revue systématique · PRISMA 2020</div>"

        "<form method='post' action='/run'>"

        "<label for='q'>Question de recherche</label>"

        "<textarea id='q' name='question' rows='6' required "
        "placeholder='Does physical exercise reduce depressive symptoms in adults?'>"
        "</textarea>"

        "<label for='p'>Protocole YAML (optionnel)</label>"

        "<input id='p' type='text' name='protocol' "
        "value='config/protocol_template.yaml'>"

        "<label class='chk'>"
        "<input type='checkbox' name='use_protocol' checked>"
        "Utiliser le protocole"
        "</label>"

        "<button class='primary'"
        + (" disabled" if running else "")
        + ">▶ Lancer le pipeline</button>"

        "</form>"
    )

    if runs:

        options = ""

        for run in runs:

            selected = (
                " selected"
                if run == selected_run
                else ""
            )

            options += (
                f"<option value='{esc(run)}'{selected}>"
                f"{esc(run)}</option>"
            )

        content += (
            "<label for='r'>Exécutions précédentes</label>"
            "<select id='r' "
            "onchange=\"location.href='/?run='+encodeURIComponent(this.value)\">"
            + options
            + "</select>"
        )

    return content


# ============================================================
# RUN PANEL
# ============================================================

def run_panel(state: dict[str, Any]) -> str:

    if not (
        state["running"]
        or state["returncode"] is not None
    ):
        return ""

    stop_button = ""

    if state["running"]:

        stop_button = (
            "<form method='post' action='/stop'>"
            "<button>■ Arrêter</button>"
            "</form>"
        )

    label = (
        "Exécution en cours…"
        if state["running"]
        else (
            "Terminé ✔"
            if state["returncode"] == 0
            else f"Échec (code {state['returncode']})"
        )
    )

    return (
        "<details class='logp'"
        + (" open" if state["running"] else "")
        + ">"
        "<summary><span id='st'>"
        + esc(label)
        + "</span></summary>"
        "<pre id='log'>"
        + esc(state["log"])
        + "</pre>"
        + stop_button
        + "</details>"
        "<script>"
        + POLL_JS
        + "</script>"
    )


# ============================================================
# CONTENT
# ============================================================

def render_tab(
    tab: str,
    run: str,
    directory: Path,
    prisma: dict[str, Any],
) -> str:

    if tab == "Report":
        return tab_report(directory)

    if tab == "PRISMA":
        return tab_prisma(prisma)

    if tab == "Screening":
        return tab_screening(directory)

    if tab == "Evidence table":
        return tab_evidence(directory)

    if tab == "Critic":
        return tab_critic(directory)

    if tab == "Audit":
        return tab_audit(directory)

    if tab == "Downloads":
        return tab_downloads(run, directory)

    return muted("Onglet inconnu.")


def main_content(
    run: str | None,
    tab: str,
) -> str:

    if not run:

        return (
            "<div class='hero'>"
            "<h1>sr-agent</h1>"
            "<p>Aucune exécution pour l'instant.</p>"
            "<p>Saisissez une question dans la barre latérale "
            "puis lancez le pipeline.</p>"
            "</div>"
        )

    directory = get_run_dir(run)

    if directory is None:
        return muted("Cette exécution n'existe pas.")

    metrics = read_run_json(directory, "metrics.json")

    if not isinstance(metrics, dict):
        metrics = {}

    prisma = (
        metrics.get("prisma")
        or read_run_json(directory, "prisma_flow.json")
        or {}
    )

    if not isinstance(prisma, dict):
        prisma = {}

    elapsed = metrics.get("elapsed_seconds")

    cards = (
        ("Identifiés", prisma.get("identified", "–")),
        ("Criblés", prisma.get("screened", "–")),
        ("Inclus", prisma.get("included_final", "–")),
        (
            "Durée (min)",
            round(elapsed / 60, 1)
            if isinstance(elapsed, (int, float))
            else "–",
        ),
    )

    cards_html = ""

    for label, value in cards:

        cards_html += (
            "<div class='card'>"
            f"<span>{esc(str(label))}</span>"
            f"<b>{esc(str(value))}</b>"
            "</div>"
        )

    navigation = ""

    for item in TABS:

        active = " on" if item == tab else ""

        navigation += (
            f"<a class='tab{active}' "
            f"href='/?run={quote(run)}&tab={quote(item)}'>"
            f"{esc(item)}</a>"
        )

    return (
        "<h1>Exécution</h1>"
        f"<p class='sub'>{esc(run)}</p>"
        f"<div class='cards'>{cards_html}</div>"
        f"<nav class='tabs'>{navigation}</nav>"
        "<section class='panel'>"
        + render_tab(tab, run, directory, prisma)
        + "</section>"
    )


# ============================================================
# PAGE
# ============================================================

def page(
    run: str | None,
    tab: str,
) -> str:

    runs = list_runs()

    # Si le run demandé est invalide, prendre le plus récent.
    if run not in runs:
        run = runs[0] if runs else None

    if tab not in TABS:
        tab = "Report"

    state = RUNNER.snapshot()

    return (
        "<!doctype html>"
        "<html lang='fr'>"
        "<head>"
        "<meta charset='utf-8'>"
        "<title>sr-agent</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>"
        + CSS
        + "</style>"
        "</head>"
        "<body>"
        "<aside>"
        + sidebar(runs, run, state["running"])
        + "</aside>"
        "<main>"
        + run_panel(state)
        + main_content(run, tab)
        + "</main>"
        "</body>"
        "</html>"
    )


# ============================================================
# HTTP SERVER
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def log_message(
        self,
        fmt: str,
        *args: Any,
    ) -> None:
        log.debug(fmt, *args)

    def _host_ok(self) -> bool:

        return (
            self.headers.get("Host") or ""
        ) in ALLOWED_HOSTS

    def _send(
        self,
        code: int,
        body: str | bytes,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:

        data = (
            body.encode("utf-8")
            if isinstance(body, str)
            else body
        )

        self.send_response(code)

        self.send_header(
            "Content-Type",
            content_type,
        )

        self.send_header(
            "Content-Length",
            str(len(data)),
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )

        self.send_header(
            "Referrer-Policy",
            "no-referrer",
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        for key, value in (headers or {}).items():
            self.send_header(key, value)

        self.end_headers()

        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _redirect(self) -> None:

        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self) -> None:

        if not self._host_ok():
            return self._send(
                403,
                "Forbidden",
                "text/plain",
            )

        try:

            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)

            def arg(name: str) -> str:
                return query.get(name, [""])[0]

            if parsed.path == "/":

                self._send(
                    200,
                    page(
                        arg("run") or None,
                        arg("tab") or "Report",
                    ),
                )

            elif parsed.path == "/status":

                self._send(
                    200,
                    json.dumps(
                        RUNNER.snapshot(),
                        ensure_ascii=False,
                    ),
                    "application/json; charset=utf-8",
                )

            elif parsed.path == "/download":

                run = arg("run")
                name = arg("name")

                directory = get_run_dir(run)

                if (
                    directory is None
                    or name not in DOWNLOADS
                ):

                    return self._send(
                        404,
                        "Not found",
                        "text/plain",
                    )

                file_path = find_artifact(
                    directory,
                    name,
                )

                if file_path is None:
                    return self._send(
                        404,
                        "File not found",
                        "text/plain",
                    )

                self._send(
                    200,
                    file_path.read_bytes(),
                    "application/octet-stream",
                    {
                        "Content-Disposition":
                        f'attachment; filename="{name}"'
                    },
                )

            else:

                self._send(
                    404,
                    "Not found",
                    "text/plain",
                )

        except Exception:

            log.exception(
                "GET error: %s",
                self.path,
            )

            self._send(
                500,
                "<pre>"
                + esc(traceback.format_exc())
                + "</pre>",
            )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self) -> None:

        if not self._host_ok():
            return self._send(
                403,
                "Forbidden",
                "text/plain",
            )

        try:

            length = int(
                self.headers.get("Content-Length") or 0
            )

            if length > 1_000_000:
                return self._send(
                    413,
                    "Request too large",
                    "text/plain",
                )

            raw = self.rfile.read(length)

            form = parse_qs(
                raw.decode("utf-8", "replace")
            )

            if self.path == "/run":

                question = form.get(
                    "question",
                    [""],
                )[0].strip()

                protocol = None

                if "use_protocol" in form:

                    protocol = safe_protocol(
                        form.get(
                            "protocol",
                            [""],
                        )[0].strip()
                    )

                if question:

                    started = RUNNER.start(
                        question,
                        protocol,
                    )

                    if not started:
                        log.warning(
                            "Pipeline non démarré: "
                            "déjà en cours ou erreur."
                        )

            elif self.path == "/stop":

                RUNNER.stop()

            self._redirect()

        except Exception:

            log.exception("POST error")

            self._send(
                500,
                "<pre>"
                + esc(traceback.format_exc())
                + "</pre>",
            )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler,
    )

    server.daemon_threads = True

    url = f"http://{HOST}:{PORT}"

    print("=" * 60)
    print("sr-agent - Lite UI")
    print(f"URL : {url}")
    print("Arrêt : Ctrl+C")
    print("=" * 60)

    threading.Timer(
        1.0,
        lambda: webbrowser.open(url),
    ).start()

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print("\nArrêt du serveur.")

    finally:

        server.server_close()


if __name__ == "__main__":
    main()
