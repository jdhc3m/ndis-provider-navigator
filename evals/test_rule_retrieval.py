"""Pricing-rules retrieval eval: hit@3 of both chunking strategies over the ten golden
questions, through the same helpers the report renders.

Embeddings go through the bounded adapter with the disk cache, so a rerun after the index
build costs no endpoint calls at all. Skips visibly while the index or the embedding cache
is missing (the full-corpus build has not run on this machine yet).
"""

from pathlib import Path

import pytest

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.golden_questions import golden_pairs, read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.application.extraction import TokenBudget
from ndis.application.pricing import (
    DEFAULT_TOP_K,
    hit_rate,
    lowest_top_similarity,
    retrieval_rows,
)
from ndis.config import DEFAULT_SIMILARITY_THRESHOLD, embed_model, llm_api_key, llm_base_url

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
INDEX = ROOT / "data" / "rule_index.db"
CACHE = ROOT / "data" / "private" / "embedding_cache.db"

# Measured 2026-11-09 against the built 2025-26 v1.1 index (reports/retrieval.md).
EXPECTED_HIT3 = {"fixed": 1.0, "heading": 1.0}


def _load_env_if_needed() -> None:
    """Best effort: source ``~/.config/ndis/.env`` when the endpoint env is absent."""
    import os

    if embed_model() and llm_base_url() and llm_api_key():
        return
    env_file = Path.home() / ".config" / "ndis" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@pytest.fixture
def index() -> SqliteVecRuleStore:
    if not INDEX.exists():
        pytest.skip(f"rule index not built yet: {INDEX} is missing")
    store = SqliteVecRuleStore(INDEX)
    yield store
    store.close()


@pytest.mark.eval
def test_hit_at_3_for_both_strategies(index: SqliteVecRuleStore) -> None:
    _load_env_if_needed()
    if not embed_model():
        pytest.skip("EMBED_MODEL is not configured on this machine")
    golden = read_golden(GOLDEN)
    cache = EmbeddingCache(CACHE)
    client = CachedEmbeddingClient(
        OpenAICompatibleClient(
            base_url=llm_base_url(),
            api_key=llm_api_key(),
            model="unused-for-embeddings",
            embed_model=embed_model(),
            budget=TokenBudget(1_000_000),
            interval=20.0,
            backoff_base=20.0,
            backoff_cap=75.0,
        ),
        cache,
        embed_model(),
    )
    try:
        pairs = golden_pairs(golden.questions)
        rows = {
            strategy: retrieval_rows(client, index, strategy, pairs, k=3)
            for strategy in ("fixed", "heading")
        }
        # The threshold must sit safely below the weakest golden question so no answerable
        # question is refused; the judge is the second gate behind it.
        off_topic_rows = retrieval_rows(
            client, index, "heading", [(golden.off_topic_question, 0)], k=DEFAULT_TOP_K
        )
    finally:
        cache.close()

    for strategy in ("fixed", "heading"):
        rate = hit_rate(rows[strategy], k=3)
        assert rate is not None
        assert rate == pytest.approx(EXPECTED_HIT3[strategy]), (
            f"hit@3 for {strategy} changed; update reports/retrieval.md and EXPECTED_HIT3"
        )

    floor = lowest_top_similarity(rows["heading"])
    off_topic_similarity = off_topic_rows[0].top_similarity
    assert floor is not None and off_topic_similarity is not None
    assert floor > off_topic_similarity
    assert DEFAULT_SIMILARITY_THRESHOLD < floor
