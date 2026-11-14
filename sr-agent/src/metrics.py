"""Small pure helpers (no network, no LLM) -> easy to unit-test."""
import re


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa between two raters' labels."""
    n = len(a)
    if n == 0 or n != len(b):
        return float("nan")
    labels = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    if pe == 1:
        return 1.0
    return (po - pe) / (1 - pe)


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def quote_in_context(quote: str, context: str) -> bool:
    """True if the (normalised) quote literally appears in the context."""
    q = norm_text(quote)
    return bool(q) and q in norm_text(context)
