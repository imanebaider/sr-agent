import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.metrics import cohen_kappa, quote_in_context  # noqa: E402
from src.rag.indexer import chunk_text  # noqa: E402
from src.tools.dedup import deduplicate, norm_doi  # noqa: E402
from src.tools.openalex import rebuild_abstract  # noqa: E402


def test_rebuild_abstract():
    inv = {"Exercise": [0], "helps": [1], "depression": [2, 4], "and": [3]}
    assert rebuild_abstract(inv) == "Exercise helps depression and depression"
    assert rebuild_abstract(None) == ""


def test_norm_doi():
    assert norm_doi("https://doi.org/10.1/ABC") == "10.1/abc"


def test_dedup_by_doi_and_title():
    recs = [
        {"title": "Exercise for depression", "doi": "https://doi.org/10.1/a"},
        {"title": "Different title entirely", "doi": "10.1/A"},
        {"title": "Exercise for Depression.", "doi": None},
        {"title": "Another study on sleep", "doi": None},
    ]
    kept, removed = deduplicate(recs)
    assert removed == 2 and len(kept) == 2


def test_kappa():
    assert cohen_kappa(["include", "exclude"], ["include", "exclude"]) == 1.0
    k = cohen_kappa(["include", "include", "exclude", "exclude"],
                    ["include", "exclude", "include", "exclude"])
    assert abs(k - 0.0) < 1e-9


def test_quote_in_context():
    assert quote_in_context("mean difference of -3.2", "We found a Mean difference of -3.2 (CI)")
    assert not quote_in_context("something else", "We found nothing")
    assert not quote_in_context("", "text")


def test_chunk_text():
    chunks = chunk_text("a " * 2000, size=1000, overlap=100)
    assert len(chunks) >= 4 and all(len(c) <= 1000 for c in chunks)
