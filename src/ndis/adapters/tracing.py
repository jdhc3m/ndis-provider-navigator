"""Optional tracing for every LLM call: the client passes through unchanged when the
Langfuse keys are absent, and through an ``@observe``-wrapped delegation when they are
present, so a run without tracing configuration does not even load the SDK.

The wrapper records, per call: the model id, the token usage the endpoint reported, the
call latency (the decorator times it) and the pipeline step that made the call. Tracing
is best-effort — a tracing failure never fails the model call it describes.
"""

import os
from collections.abc import Sequence

from ndis.application.extraction import LlmRequest, LlmResult, TokenBudget
from ndis.application.pricing import EmbeddingClient, EmbeddingsResult

PUBLIC_KEY_ENV = "LANGFUSE_PUBLIC_KEY"
SECRET_KEY_ENV = "LANGFUSE_SECRET_KEY"
# The SDK reads the base URL from LANGFUSE_BASE_URL, falling back to the older
# LANGFUSE_HOST and then to the hosted service; this mirrors that order.
BASE_URL_ENV = "LANGFUSE_BASE_URL"
HOST_ENV = "LANGFUSE_HOST"
DEFAULT_HOST = "https://cloud.langfuse.com"


def langfuse_enabled() -> bool:
    """True when both Langfuse keys are configured; nothing else is required."""
    return bool(
        os.environ.get(PUBLIC_KEY_ENV, "").strip() and os.environ.get(SECRET_KEY_ENV, "").strip()
    )


def tracing_url() -> str | None:
    """Where the traces can be viewed, or ``None`` when tracing is off: the same host the
    SDK would send to (``LANGFUSE_BASE_URL``, then ``LANGFUSE_HOST``, then its default)."""
    if not langfuse_enabled():
        return None
    return (
        os.environ.get(BASE_URL_ENV, "").strip()
        or os.environ.get(HOST_ENV, "").strip()
        or DEFAULT_HOST
    )


def traced(client: EmbeddingClient, site: str) -> EmbeddingClient:
    """The same client when tracing is off; a tracing delegation when it is on. ``site``
    names the pipeline step (``intent_parse``, ``rule_judge``, ``extraction``, ...) the
    client serves, so the trace names the caller and not just the model."""
    if not langfuse_enabled():
        return client
    return TracedLlmClient(client, site)


class TracedLlmClient:
    """Delegating ``LlmClient``/``EmbeddingClient`` whose every call is a Langfuse
    observation with token/latency metadata."""

    def __init__(self, inner: EmbeddingClient, site: str) -> None:
        self._inner = inner
        self._site = site
        self._model = getattr(inner, "model", "")
        self._embed_model = getattr(inner, "embed_model", "") or ""

    def complete(self, request: LlmRequest) -> LlmResult:
        from ndis.adapters.langfuse_bridge import traced_complete

        return traced_complete(self._inner, request, self._model, self._site)

    def embed(self, texts: Sequence[str]) -> EmbeddingsResult:
        from ndis.adapters.langfuse_bridge import traced_embed

        return traced_embed(self._inner, texts, self._embed_model, self._site)

    @property
    def budget(self) -> TokenBudget:
        return self._inner.budget

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


def flush() -> None:
    """Push pending observations; a no-op when tracing is off."""
    if langfuse_enabled():
        from ndis.adapters.langfuse_bridge import flush as bridge_flush

        bridge_flush()
