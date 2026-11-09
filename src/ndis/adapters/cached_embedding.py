"""An ``EmbeddingClient`` that checks the disk cache before the endpoint.

Free-tier embedding endpoints are rate-limited per minute and per day, so every build and
eval run goes through this wrapper: texts already embedded (same model, same bytes) answer
from the cache and only the remainder reach the bounded client. The cache is content-hash
keyed, so a resumed run never pays twice for a chunk.
"""

from collections.abc import Sequence

from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.application.extraction import TokenBudget
from ndis.application.pricing import EmbeddingClient, EmbeddingsResult


class CachedEmbeddingClient:
    """Cache-first decorator around one bounded embedding client."""

    def __init__(self, inner: EmbeddingClient, cache: EmbeddingCache, model: str) -> None:
        self._inner = inner
        self._cache = cache
        self._model = model

    @property
    def budget(self) -> TokenBudget:
        """The inner client's budget, for run summaries."""
        return self._inner.budget

    def embed(self, texts: Sequence[str]) -> EmbeddingsResult:
        texts = list(texts)
        if not texts:
            return EmbeddingsResult(vectors=(), input_tokens=0)
        cached = self._cache.get(self._model, texts)
        missing = [text for text, vector in zip(texts, cached, strict=True) if vector is None]
        fresh: dict[int, tuple[float, ...]] = {}
        input_tokens = 0
        if missing:
            result = self._inner.embed(missing)
            if len(result.vectors) != len(missing):
                raise ValueError("embedding endpoint returned the wrong number of vectors")
            fresh = {
                index: vector
                for index, vector in zip(
                    (i for i, vector in enumerate(cached) if vector is None),
                    result.vectors,
                    strict=True,
                )
            }
            self._cache.put(self._model, missing, list(result.vectors))
            input_tokens = result.input_tokens
        vectors = tuple(
            cached[index] if cached[index] is not None else fresh[index]
            for index in range(len(texts))
        )
        return EmbeddingsResult(vectors=vectors, input_tokens=input_tokens)
