"""Environment-driven settings for the model-backed adapters.

This module is the only place allowed to talk about vendors and models, and even here the
values come from the environment (``~/.config/ndis/.env`` sourced before the run): no
default points at any provider. The front end later reads model names from ``GET /meta``,
never from code.
"""

import os
from pathlib import Path


def llm_base_url() -> str:
    """Root of an OpenAI-compatible chat endpoint, e.g. ``https://host/v1/``."""
    return os.environ.get("LLM_BASE_URL", "").strip()


def llm_model() -> str:
    """The generation model id the endpoint should use."""
    return os.environ.get("LLM_MODEL", "").strip()


def llm_api_key() -> str:
    """The personal API key for the endpoint."""
    return os.environ.get("LLM_API_KEY", "").strip()


def llm_configured() -> bool:
    """True when base URL, model and key are all set."""
    return all((llm_base_url(), llm_model(), llm_api_key()))


def embed_model() -> str:
    """The embedding model id the endpoint should use."""
    return os.environ.get("EMBED_MODEL", "").strip()


def embed_configured() -> bool:
    """True when base URL, embedding model and key are all set."""
    return all((llm_base_url(), embed_model(), llm_api_key()))


# The refusal threshold for rule questions: a best-chunk cosine similarity below this
# refuses before any model call. Measured on the golden set in reports/retrieval.md;
# on-topic questions score well above it, off-topic ones well below.
DEFAULT_SIMILARITY_THRESHOLD = 0.35


def rule_similarity_threshold() -> float:
    """The minimum top-chunk cosine similarity for a rule answer (configurable)."""
    raw = os.environ.get("RULE_SIMILARITY_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_SIMILARITY_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_SIMILARITY_THRESHOLD


def rule_chunk_strategy() -> str:
    """The chunking strategy the served index answers from (``fixed`` or ``heading``)."""
    return os.environ.get("RULE_CHUNK_STRATEGY", "").strip() or "heading"


def rule_index_path() -> Path:
    """The sqlite-vec index file the API reads."""
    return Path(os.environ.get("RULE_INDEX_PATH", "").strip() or "data/rule_index.db")


def rule_top_k() -> int:
    """How many chunks are retrieved per rule question."""
    raw = os.environ.get("RULE_TOP_K", "").strip()
    if not raw:
        return 6
    try:
        return max(1, int(raw))
    except ValueError:
        return 6
