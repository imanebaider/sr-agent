"""Download an open-access PDF and extract its text."""
from io import BytesIO

import requests
from pypdf import PdfReader


def fetch_fulltext(url: str, max_chars: int = 60000) -> str | None:
    try:
        r = requests.get(url, timeout=40, headers={"User-Agent": "sr-agent/1.0"})
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            return None
        reader = PdfReader(BytesIO(r.content))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
        text = text.strip()
        return text[:max_chars] if len(text) > 500 else None
    except Exception:  # noqa: BLE001
        return None
