"""OpenAlex client (free, no API key). Docs: https://docs.openalex.org"""
import re
import time

import requests

BASE = "https://api.openalex.org/works"


def clean_query(q: str) -> str:
    """Remove PubMed-style syntax that OpenAlex rejects with a 400 error."""
    q = q.replace("\\", "")                      # tai\ chi -> tai chi
    q = re.sub(r"\b\w+:\S+", " ", q)             # field:value (population:adult*)
    q = q.replace("*", "").replace("?", "")      # wildcards
    q = re.sub(r'"\s*18\s*\+\s*"', " ", q)       # "18+"
    q = re.sub(r"\[[^\]]*\]", " ", q)            # [MeSH] tags
    for _ in range(3):                           # clean empty groups / dangling operators
        q = re.sub(r"\(\s*\)", " ", q)
        q = re.sub(r"\(\s*(AND|OR)\b", "(", q)
        q = re.sub(r"\b(AND|OR)\s*\)", ")", q)
        q = re.sub(r"^\s*(AND|OR)\b", " ", q)
        q = re.sub(r"\b(AND|OR)\s*$", " ", q)
        q = re.sub(r"\b(AND|OR)\s+(AND|OR)\b", r"\1", q)
    return re.sub(r"\s+", " ", q).strip()


def rebuild_abstract(inverted_index: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild plain text."""
    if not inverted_index:
        return ""
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def _normalize(w: dict) -> dict:
    authors = [a["author"]["display_name"] for a in (w.get("authorships") or [])[:6]
               if a.get("author", {}).get("display_name")]
    loc = w.get("best_oa_location") or {}
    journal = ((w.get("primary_location") or {}).get("source") or {}).get("display_name", "")
    return {
        "openalex_id": w.get("id"),
        "title": (w.get("title") or "").strip(),
        "year": w.get("publication_year"),
        "doi": w.get("doi"),
        "abstract": rebuild_abstract(w.get("abstract_inverted_index")),
        "authors": authors,
        "journal": journal,
        "oa_pdf_url": loc.get("pdf_url"),
        "source": "openalex",
    }


def search_openalex(query: str, n: int = 50, year_from: int | None = None,
                    mailto: str = "") -> list[dict]:
    filters = ["has_abstract:true"]
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    per_page = min(n, 100)
    results: list[dict] = []
    page = 1
    search_text = clean_query(query)
    while len(results) < n:
        params = {"search": search_text, "per-page": per_page, "page": page,
                  "filter": ",".join(filters)}
        if mailto:
            params["mailto"] = mailto
        for attempt in range(3):
            try:
                r = requests.get(BASE, params=params, timeout=30)
                if r.status_code == 400:
                    # not retryable: surface OpenAlex's own message in audit.jsonl
                    raise RuntimeError(f"OpenAlex 400 for '{search_text}': {r.text[:300]}")
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))
        batch = r.json().get("results", [])
        if not batch:
            break
        results.extend(_normalize(w) for w in batch)
        page += 1
    return results[:n]