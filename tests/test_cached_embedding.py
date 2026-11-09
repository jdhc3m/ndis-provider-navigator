"""The cache-first embedding wrapper around the bounded client."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.application.extraction import TokenBudget
from ndis.application.pricing import EmbeddingError, EmbeddingsResult


class CountingEmbedder:
    """A fake endpoint that records every text it was asked to embed."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.fail = False

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(10**9)

    def embed(self, texts: Sequence[str]) -> EmbeddingsResult:
        if self.fail:
            raise EmbeddingError("endpoint down")
        self.batches.append(list(texts))
        return EmbeddingsResult(
            vectors=tuple((float(len(text)), 1.0) for text in texts),
            input_tokens=sum(len(text) for text in texts) // 4 + 1,
        )


@pytest.fixture
def cache(tmp_path: Path) -> EmbeddingCache:
    cache = EmbeddingCache(tmp_path / "cache.db")
    yield cache
    cache.close()


def test_first_call_embeds_everything_and_caches(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    client = CachedEmbeddingClient(inner, cache, "model-x")
    result = client.embed(["alpha", "beta"])
    assert inner.batches == [["alpha", "beta"]]
    assert result.vectors == ((5.0, 1.0), (4.0, 1.0))
    assert cache.count() == 2


def test_second_call_answers_from_the_cache_only(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    client = CachedEmbeddingClient(inner, cache, "model-x")
    client.embed(["alpha", "beta"])
    second = client.embed(["beta", "alpha"])
    assert inner.batches == [["alpha", "beta"]]
    assert second.vectors == ((4.0, 1.0), (5.0, 1.0))
    assert second.input_tokens == 0


def test_only_the_misses_reach_the_endpoint(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    client = CachedEmbeddingClient(inner, cache, "model-x")
    client.embed(["alpha", "beta"])
    client.embed(["alpha", "gamma", "beta"])
    assert inner.batches == [["alpha", "beta"], ["gamma"]]


def test_cache_is_keyed_by_model(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    first = CachedEmbeddingClient(inner, cache, "model-x")
    second = CachedEmbeddingClient(inner, cache, "model-y")
    first.embed(["alpha"])
    second.embed(["alpha"])
    assert inner.batches == [["alpha"], ["alpha"]]
    assert cache.count() == 2


def test_empty_batch_never_touches_anything(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    client = CachedEmbeddingClient(inner, cache, "model-x")
    result = client.embed([])
    assert result.vectors == ()
    assert inner.batches == []


def test_endpoint_failure_leaves_the_cache_untouched(cache: EmbeddingCache) -> None:
    inner = CountingEmbedder()
    inner.fail = True
    client = CachedEmbeddingClient(inner, cache, "model-x")
    with pytest.raises(EmbeddingError):
        client.embed(["alpha"])
    assert cache.count() == 0
