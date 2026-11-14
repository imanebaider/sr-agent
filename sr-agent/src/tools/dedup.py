"""Deduplication by DOI, then by fuzzy title similarity."""
import re
from difflib import SequenceMatcher


def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip().lower())


def norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (t or "").lower())).strip()


def deduplicate(records: list[dict], threshold: float = 0.93) -> tuple[list[dict], int]:
    kept: list[dict] = []
    kept_titles: list[str] = []
    seen_dois: set[str] = set()
    removed = 0
    for r in records:
        doi = norm_doi(r.get("doi"))
        title = norm_title(r.get("title", ""))
        if not title:
            removed += 1
            continue
        if doi and doi in seen_dois:
            removed += 1
            continue
        dup = any(
            abs(len(title) - len(t)) < 15 and SequenceMatcher(None, title, t).ratio() >= threshold
            for t in kept_titles
        )
        if dup:
            removed += 1
            continue
        if doi:
            seen_dois.add(doi)
        kept.append(r)
        kept_titles.append(title)
    return kept, removed
