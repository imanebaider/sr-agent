"""Sources de littérature supplémentaires pour sr-agent : PubMed, Semantic Scholar, arXiv.

OpenAlex reste géré par le code existant. Ce module renvoie des enregistrements
normalisés (dict) que retrieval.py peut fusionner avec ceux d'OpenAlex avant la
déduplication (DOI + titre approximatif).

Variables .env (toutes optionnelles) :
    EXTRA_SOURCES=pubmed,semantic_scholar      # ou ajouter ,arxiv
    OPENALEX_EMAIL=...                         # réutilisé comme email pour PubMed
    PUBMED_API_KEY=...                         # 10 req/s au lieu de 3
    S2_API_KEY=...                             # évite les 429 de Semantic Scholar

Test rapide :
    python -m src.tools.sources "mindfulness chronic pain"
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

log = logging.getLogger(__name__)

TIMEOUT = 30
HEADERS = {"User-Agent": "sr-agent/1.0 (systematic review research tool)"}


# ---------------------------------------------------------------- utilitaires

def _get(url: str, params: dict | None = None, headers: dict | None = None,
         retries: int = 4, pause: float = 2.0) -> requests.Response | None:
    """GET avec retry exponentiel sur 429 / 5xx. Renvoie None si échec final."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT,
                             headers={**HEADERS, **(headers or {})})
            if r.status_code == 429 or r.status_code >= 500:
                wait = pause * (2 ** attempt)
                log.warning("HTTP %s sur %s, nouvel essai dans %.0fs", r.status_code, url, wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            log.warning("Erreur réseau sur %s : %s", url, exc)
            time.sleep(pause * (2 ** attempt))
    return None


def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi or None


def _record(source: str, source_id: str, title: str | None, abstract: str | None,
            year: int | None, authors: list[str], doi: str | None,
            venue: str | None, url: str | None, pdf_url: str | None) -> dict[str, Any]:
    return {
        "id": f"{source}:{source_id}",
        "source": source,
        "title": " ".join((title or "").split()),
        "abstract": " ".join((abstract or "").split()),
        "year": year,
        "authors": authors,
        "doi": _norm_doi(doi),
        "venue": venue or "",
        "url": url or "",
        "pdf_url": pdf_url or "",
    }


def _plain(query: str) -> str:
    """Transforme une requête booléenne style PubMed en mots-clés simples.

    Semantic Scholar et arXiv ne comprennent ni [MeSH Terms] ni les [tiab].
    """
    q = re.sub(r"\[[^\]]*\]", " ", query)
    q = re.sub(r"\b(AND|OR|NOT)\b", " ", q)
    q = re.sub(r"[()\"]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _year(text: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m.group(0)) if m else None


# --------------------------------------------------------------------- PubMed

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_pubmed(query: str, max_results: int = 100,
                  email: str | None = None, api_key: str | None = None) -> list[dict]:
    """PubMed via E-utilities. Accepte la syntaxe booléenne/MeSH native."""
    email = email or os.environ.get("OPENALEX_EMAIL")
    api_key = api_key or os.environ.get("PUBMED_API_KEY")
    base = {"tool": "sr-agent"}
    if email:
        base["email"] = email
    if api_key:
        base["api_key"] = api_key
    delay = 0.12 if api_key else 0.4

    r = _get(f"{EUTILS}/esearch.fcgi",
             {**base, "db": "pubmed", "term": query, "retmax": max_results,
              "retmode": "json", "sort": "relevance"})
    if r is None:
        return []
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    records: list[dict] = []

    for i in range(0, len(ids), 100):
        time.sleep(delay)
        batch = ids[i:i + 100]
        r = _get(f"{EUTILS}/efetch.fcgi",
                 {**base, "db": "pubmed", "id": ",".join(batch),
                  "retmode": "xml", "rettype": "abstract"})
        if r is None:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            log.warning("XML PubMed illisible pour le lot %s", i)
            continue

        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//MedlineCitation/PMID") or ""
            title_el = art.find(".//Article/ArticleTitle")
            title = "".join(title_el.itertext()) if title_el is not None else ""

            parts = []
            for ab in art.findall(".//Article/Abstract/AbstractText"):
                text = "".join(ab.itertext()).strip()
                label = ab.get("Label")
                parts.append(f"{label}: {text}" if label else text)

            pub = art.find(".//Article/Journal/JournalIssue/PubDate")
            year = None
            if pub is not None:
                year = _year(pub.findtext("Year") or pub.findtext("MedlineDate"))

            authors = []
            for a in art.findall(".//Article/AuthorList/Author"):
                last, ini = a.findtext("LastName"), a.findtext("Initials")
                if last:
                    authors.append(f"{last} {ini}".strip() if ini else last)

            doi = None
            for aid in art.findall(".//PubmedData/ArticleIdList/ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text
                    break

            records.append(_record(
                "pubmed", pmid, title, " ".join(parts), year, authors, doi,
                art.findtext(".//Article/Journal/Title"),
                f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", None))
    return records


# ------------------------------------------------------------ Semantic Scholar

S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,authors,externalIds,venue,url,openAccessPdf"


def search_semantic_scholar(query: str, max_results: int = 100,
                            api_key: str | None = None) -> list[dict]:
    """Semantic Scholar Graph API (recherche par mots-clés, pas de MeSH)."""
    api_key = api_key or os.environ.get("S2_API_KEY")
    headers = {"x-api-key": api_key} if api_key else {}
    q = _plain(query)
    records: list[dict] = []
    offset = 0

    while len(records) < max_results and offset < 1000:
        limit = min(100, max_results - len(records))
        r = _get(S2_URL, {"query": q, "limit": limit, "offset": offset,
                          "fields": S2_FIELDS}, headers=headers, pause=3.0)
        if r is None:
            break
        data = r.json()
        for p in data.get("data", []):
            ext = p.get("externalIds") or {}
            oa = p.get("openAccessPdf") or {}
            records.append(_record(
                "s2", p.get("paperId", ""), p.get("title"), p.get("abstract"),
                p.get("year"), [a.get("name", "") for a in p.get("authors", [])],
                ext.get("DOI"), p.get("venue"), p.get("url"), oa.get("url")))
        if "next" not in data:
            break
        offset = data["next"]
        time.sleep(0.2 if api_key else 1.2)
    return records


# ---------------------------------------------------------------------- arXiv

ARXIV_URL = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def search_arxiv(query: str, max_results: int = 50) -> list[dict]:
    """arXiv (utile surtout pour l'informatique, la physique, les maths)."""
    words = [w for w in _plain(query).split() if len(w) > 2][:6]
    if not words:
        return []
    search = " AND ".join(f"all:{w}" for w in words)

    r = _get(ARXIV_URL, {"search_query": search, "start": 0,
                         "max_results": min(max_results, 100),
                         "sortBy": "relevance"}, pause=3.0)
    if r is None:
        return []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []

    records = []
    for e in root.findall("a:entry", NS):
        link = e.findtext("a:id", default="", namespaces=NS)
        arxiv_id = link.rsplit("/abs/", 1)[-1]
        pdf = next((l.get("href") for l in e.findall("a:link", NS)
                    if l.get("title") == "pdf"), None)
        records.append(_record(
            "arxiv", arxiv_id, e.findtext("a:title", namespaces=NS),
            e.findtext("a:summary", namespaces=NS),
            _year(e.findtext("a:published", namespaces=NS)),
            [a.findtext("a:name", default="", namespaces=NS)
             for a in e.findall("a:author", NS)],
            e.findtext("x:doi", namespaces=NS), "arXiv", link, pdf))
    time.sleep(3)  # politesse envers arXiv
    return records


# ------------------------------------------------------------------ point d'entrée

def _dedup_local(records: list[dict]) -> list[dict]:
    """Déduplication simple (DOI, sinon titre normalisé). La vraie dédup reste dans retrieval.py."""
    seen: set[str] = set()
    out = []
    for r in records:
        if not r["title"]:
            continue
        key = r["doi"] or re.sub(r"\W+", "", r["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def search_extra_sources(queries: list[str], sources: list[str] | None = None,
                         max_per_source: int = 100) -> list[dict]:
    """Interroge les sources demandées avec toutes les requêtes.

    Renvoie aussi, dans chaque record, la clé 'source' pour que le PRISMA
    puisse compter les enregistrements par base de données.
    """
    if sources is None:
        env = os.environ.get("EXTRA_SOURCES", "pubmed,semantic_scholar")
        sources = [s.strip().lower() for s in env.split(",") if s.strip()]
    if not queries:
        return []

    per_query = max(10, max_per_source // len(queries))
    runners = {
        "pubmed": lambda q: search_pubmed(q, per_query),
        "semantic_scholar": lambda q: search_semantic_scholar(q, per_query),
        "arxiv": lambda q: search_arxiv(q, per_query),
    }

    all_records: list[dict] = []
    for name in sources:
        run = runners.get(name)
        if run is None:
            log.warning("Source inconnue ignorée : %s", name)
            continue
        found: list[dict] = []
        for q in queries:
            found.extend(run(q))
        found = _dedup_local(found)[:max_per_source]
        log.info("%s : %d enregistrements", name, len(found))
        all_records.extend(found)
    return all_records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    q = " ".join(sys.argv[1:]) or "mindfulness chronic pain"
    for rec in search_extra_sources([q], max_per_source=5):
        print(f"[{rec['source']}] {rec['year']} | {rec['title'][:90]} | doi={rec['doi']}")
