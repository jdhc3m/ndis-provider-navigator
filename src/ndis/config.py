"""Environment-driven settings for the model-backed adapters.

This module is the only place allowed to talk about vendors and models, and even here the
values come from the environment (``~/.config/ndis/.env`` sourced before the run): no
default points at any provider. The front end later reads model names from ``GET /meta``,
never from code.
"""

import os


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
