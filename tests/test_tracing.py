"""The tracing wrapper: pass-through without keys, @observe-decorated delegation with
them, and enrichment that records model, usage and site without ever failing the call."""

import os
from dataclasses import replace

import pytest

from ndis.adapters import langfuse_bridge, tracing
from ndis.application.extraction import LlmRequest, LlmResult, TokenBudget
from ndis.application.pricing import EmbeddingsResult

REQUEST = LlmRequest(system="system prompt", user="user prompt")


class FakeClient:
    """The LlmClient/EmbeddingClient surface the wrapper delegates to."""

    def __init__(self) -> None:
        self.completed: list[LlmRequest] = []
        self.embedded: list[list[str]] = []
        self.closed = False

    def complete(self, request: LlmRequest) -> LlmResult:
        self.completed.append(request)
        return LlmResult("{}", 11, 7)

    def embed(self, texts: list[str]) -> EmbeddingsResult:
        self.embedded.append(texts)
        return EmbeddingsResult(vectors=([0.0, 0.0],), input_tokens=5)

    @property
    def model(self) -> str:
        return "model-from-config"

    @property
    def embed_model(self) -> str:
        return "embed-from-config"

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(100, spent=3)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tracing.PUBLIC_KEY_ENV, raising=False)
    monkeypatch.delenv(tracing.SECRET_KEY_ENV, raising=False)


def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tracing.PUBLIC_KEY_ENV, "pk-test")
    monkeypatch.setenv(tracing.SECRET_KEY_ENV, "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")


def test_without_keys_the_client_passes_through_unchanged(no_keys: None) -> None:
    inner = FakeClient()
    assert tracing.langfuse_enabled() is False
    assert tracing.traced(inner, "intent_parse") is inner


def test_enabled_requires_both_keys(monkeypatch: pytest.MonkeyPatch, no_keys: None) -> None:
    monkeypatch.setenv(tracing.PUBLIC_KEY_ENV, "pk-test")
    assert tracing.langfuse_enabled() is False
    keys(monkeypatch)
    assert tracing.langfuse_enabled() is True


def test_completion_delegates_and_enriches(monkeypatch: pytest.MonkeyPatch, no_keys: None) -> None:
    keys(monkeypatch)
    inner = FakeClient()
    enriched: list[dict[str, object]] = []
    monkeypatch.setattr(
        langfuse_bridge, "enrich_generation", lambda **fields: enriched.append(fields)
    )
    wrapped = tracing.TracedLlmClient(inner, "intent_parse")
    result = wrapped.complete(REQUEST)
    assert result == LlmResult("{}", 11, 7)
    assert inner.completed == [REQUEST]
    assert enriched == [
        {
            "name": "intent_parse",
            "model": "model-from-config",
            "input": {
                "system": {"head": "system prompt", "chars": 13},
                "user": {"head": "user prompt", "chars": 11},
            },
            "output": {"head": "{}", "chars": 2},
            "usage": {"input": 11, "output": 7},
            "metadata": {
                "site": "intent_parse",
                "endpoint": "chat/completions",
                "endpoint_seconds": None,
            },
        }
    ]
    wrapped.close()
    assert inner.closed


def test_embedding_delegates_and_enriches(monkeypatch: pytest.MonkeyPatch, no_keys: None) -> None:
    keys(monkeypatch)
    inner = FakeClient()
    enriched: list[dict[str, object]] = []
    monkeypatch.setattr(
        langfuse_bridge, "enrich_generation", lambda **fields: enriched.append(fields)
    )
    wrapped = tracing.TracedLlmClient(inner, "rule_embedding")
    result = wrapped.embed(["one", "two"])
    assert result == EmbeddingsResult(vectors=([0.0, 0.0],), input_tokens=5)
    assert inner.embedded == [["one", "two"]]
    assert enriched == [
        {
            "name": "rule_embedding",
            "model": "embed-from-config",
            "input": {"batch_size": 2, "chars": 6},
            "output": {"vectors": 1, "dim": 2},
            "usage": {"input": 5},
            "metadata": {
                "site": "rule_embedding",
                "endpoint": "embeddings",
                "endpoint_seconds": None,
            },
        }
    ]


def test_budget_delegates(no_keys: None) -> None:
    inner = FakeClient()
    assert tracing.TracedLlmClient(inner, "extraction").budget == replace(TokenBudget(100), spent=3)


def test_enrichment_swallows_tracing_failures(
    monkeypatch: pytest.MonkeyPatch, no_keys: None
) -> None:
    """A tracing failure must never fail the model call it describes."""

    def boom(**fields: object) -> None:
        raise RuntimeError("langfuse down")

    monkeypatch.setattr(langfuse_bridge, "get_client", boom)
    langfuse_bridge.enrich_generation(
        name="s", model="m", input={}, output={}, usage={}, metadata={}
    )


def test_flush_is_a_no_op_without_keys(no_keys: None, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def bridge_flush() -> None:
        called.append(True)

    monkeypatch.setattr(langfuse_bridge, "flush", bridge_flush)
    tracing.flush()
    assert called == []
    keys(monkeypatch)
    tracing.flush()
    assert called == [True]


def test_full_enabled_path_records_and_survives_a_dead_host(
    monkeypatch: pytest.MonkeyPatch, no_keys: None
) -> None:
    """The real @observe path with unreachable tracing: the call itself must succeed."""
    keys(monkeypatch)
    inner = FakeClient()
    result = tracing.TracedLlmClient(inner, "intent_parse").complete(REQUEST)
    assert result == LlmResult("{}", 11, 7)
    assert os.environ[tracing.PUBLIC_KEY_ENV] == "pk-test"
