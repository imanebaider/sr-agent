"""Provider-agnostic LLM helpers: structured output + retries + throttling + audit logging.

LLM_PROVIDER in .env: anthropic | gemini | groq | ollama
"""
import re
import threading
import time

from src.audit import log_event
from src.config import LLM_MIN_INTERVAL, MODEL_NAME, PROVIDER

_lock = threading.Lock()
_last = [0.0]


def _throttle() -> None:
    """Keep a minimum delay between calls (useful for free-tier rate limits)."""
    if LLM_MIN_INTERVAL <= 0:
        return
    with _lock:
        wait = _last[0] + LLM_MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def get_llm():
    if PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        # claude-sonnet-5-5 only accepts the default temperature -> not passed
        return ChatAnthropic(model=MODEL_NAME, max_tokens=4096)
    if PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0)  # needs GOOGLE_API_KEY
    if PROVIDER == "groq":
        from langchain_groq import ChatGroq
        # max_tokens caps the tokens Groq reserves per request (free tier: 8000 TPM);
        # low reasoning effort keeps gpt-oss from burning tokens on hidden reasoning.
        return ChatGroq(model=MODEL_NAME, temperature=0, max_tokens=2000,
                        reasoning_effort="low")  # needs GROQ_API_KEY
    if PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=MODEL_NAME, temperature=0)  # local, no key
    raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER}")


def _structured(schema):
    llm = get_llm()

    if PROVIDER in ("anthropic", "groq"):
        return llm.with_structured_output(
            schema,
            method="json_schema"
        )

    return llm.with_structured_output(schema)


def _retry_sleep(attempt: int, err: Exception) -> None:
    msg = str(err)
    rate_limited = "429" in msg or "rate" in msg.lower() or "quota" in msg.lower()
    wait = (20 if rate_limited else 2) * (attempt + 1)
    # Use the delay the API asks for when present: "try again in 4.2s" / "1m3s" / "350ms"
    m = re.search(r"try again in (?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?", msg)
    if m and any(m.groups()):
        wait = (int(m.group(1) or 0) * 60 + float(m.group(2) or 0)
                + float(m.group(3) or 0) / 1000 + 1)
    print(f"[LLM retry {attempt + 1}/5, waiting {wait:.0f}s] {type(err).__name__}: "
          f"{msg[:700]}", flush=True)
    time.sleep(wait)


def ask_structured(schema, system: str, user: str, *, temperature: float | None = None,
                   run_dir: str | None = None, agent: str = "", tag: str = ""):
    llm = _structured(schema)
    last = None
    for attempt in range(5):
        try:
            _throttle()
            out = llm.invoke([("system", system), ("human", user)])
            if out is None:
                raise ValueError("empty structured output")
            if run_dir:
                log_event(run_dir, agent, "llm_call", tag=tag, provider=PROVIDER, model=MODEL_NAME,
                          prompt=user[:1500], output=out.model_dump())
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            _retry_sleep(attempt, e)
    raise RuntimeError(f"LLM call failed after 5 attempts: {last}") from last


def ask_text(system: str, user: str, *, temperature: float | None = None,
             run_dir: str | None = None, agent: str = "", tag: str = "") -> str:
    llm = get_llm()
    last = None
    for attempt in range(5):
        try:
            _throttle()
            out = llm.invoke([("system", system), ("human", user)])
            text = out.content if isinstance(out.content, str) else "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in out.content)
            if run_dir:
                log_event(run_dir, agent, "llm_call", tag=tag, provider=PROVIDER, model=MODEL_NAME,
                          prompt=user[:1500], output=text[:3000])
            return text
        except Exception as e:  # noqa: BLE001
            last = e
            _retry_sleep(attempt, e)
    raise RuntimeError(f"LLM call failed after 5 attempts: {last}") from last