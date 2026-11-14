"""Central configuration (read from .env)."""
import os

from dotenv import load_dotenv

load_dotenv()

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower()
_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-2.0-flash",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "llama3.1:8b",
}
MODEL_NAME = os.getenv("MODEL_NAME") or _DEFAULT_MODELS.get(PROVIDER, "")
LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "0"))  # seconds between LLM calls
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
SCREEN_WORKERS = int(os.getenv("SCREEN_WORKERS", "4"))
MAX_INCLUDED = int(os.getenv("MAX_INCLUDED", "30"))
MAX_RECORDS = int(os.getenv("MAX_RECORDS", "100"))
MAX_REVISIONS = 1  # how many times the Critic can send the report back to the writer
