"""The Langfuse bridge: the ``@observe``-decorated wrappers every LLM call goes through
when tracing keys are configured.

This module is imported only from ``adapters.tracing`` and only when both Langfuse keys
are present in the environment, so a run without tracing never loads the SDK at all.
Each wrapper records what the evaluation gate needs — model, token usage, latency (the
decorator times the call) and which pipeline step made the call — and records prompt
content as head-truncated text with full character counts: page text and answers are
summarised, not dumped, because the private crawls should not be replicated into a
hosted tracing service. Tracing is best-effort by design: a tracing failure must never
fail the call it wraps.
"""

from collections.abc import Sequence, Sized
from typing import Any

from langfuse import get_client, observe

from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.pricing import EmbeddingsResult

INPUT_HEAD_CHARS = 500
OUTPUT_HEAD_CHARS = 200


@observe(as_type="generation", capture_input=False, capture_output=False)
def traced_complete(inner: Any, request: LlmRequest, model: str, site: str) -> LlmResult:
    """One chat completion through the wrapped client, traced with its usage and timing."""
    result = inner.complete(request)
    enrich_generation(
        name=site,
        model=model,
        input={"system": _head(request.system), "user": _head(request.user)},
        output=_head(result.text),
        usage={"input": result.input_tokens, "output": result.output_tokens},
        metadata={
            "site": site,
            "endpoint": "chat/completions",
            "endpoint_seconds": _endpoint_seconds(inner),
        },
    )
    return result


@observe(as_type="generation", capture_input=False, capture_output=False)
def traced_embed(inner: Any, texts: Sequence[str], model: str, site: str) -> EmbeddingsResult:
    """One embeddings batch through the wrapped client, traced with its usage and timing."""
    result = inner.embed(texts)
    enrich_generation(
        name=site,
        model=model,
        input={"batch_size": len(texts), "chars": sum(len(text) for text in texts)},
        output={"vectors": len(result.vectors), "dim": _dim(result.vectors)},
        usage={"input": result.input_tokens},
        metadata={
            "site": site,
            "endpoint": "embeddings",
            "endpoint_seconds": _endpoint_seconds(inner),
        },
    )
    return result


def enrich_generation(
    *, name: str, model: str, input: Any, output: Any, usage: dict[str, int], metadata: Any
) -> None:
    """Attach the recorded fields to the observation the decorator opened. A seam, so
    tests can observe what would be sent without a Langfuse project; a tracing failure
    is swallowed here — the model call it describes has already succeeded."""
    try:
        get_client().update_current_generation(
            name=name,
            model=model,
            input=input,
            output=output,
            usage_details=usage,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 - tracing must never break the call it wraps
        pass


def flush() -> None:
    """Push whatever the background exporter still holds; scripts call this before exit."""
    get_client().flush()


def _head(text: str) -> dict[str, Any]:
    """Head-truncated content plus the full size, for prompts and answers alike."""
    return {"head": text[:INPUT_HEAD_CHARS], "chars": len(text)}


def _endpoint_seconds(inner: Any) -> float | None:
    """The wrapped client's own successful-POST latency, when it reports one; the
    decorator still times the whole delegation either way."""
    seconds = getattr(inner, "last_endpoint_seconds", None)
    return None if seconds is None else round(seconds, 3)


def _dim(vectors: Sequence[Sized]) -> int | None:
    return len(vectors[0]) if vectors else None
