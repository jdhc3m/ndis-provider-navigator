"""The sqlite-vec rule store and the disk embedding cache on small synthetic vectors."""

import math
import struct
from pathlib import Path

import pytest

from ndis.adapters.embedding_cache import EmbeddingCache, cache_key
from ndis.adapters.rule_vector_store import (
    RuleIndexMissing,
    SqliteVecRuleStore,
    normalize,
    similarity_from_distance,
)
from ndis.domain.pricing import STRATEGY_FIXED, STRATEGY_HEADING, PricingChunk

CHUNKS = (
    PricingChunk(STRATEGY_FIXED, 12, "cancellation fee rule"),
    PricingChunk(STRATEGY_FIXED, 13, "travel claim rule"),
    PricingChunk(STRATEGY_HEADING, 14, "TTP rule"),
)
VECTORS = ((1.0, 0.0), (0.6, 0.8), (0.0, 1.0))


def test_normalize_scales_to_unit_length() -> None:
    assert normalize((3.0, 4.0)) == (0.6, 0.8)
    assert normalize((0.0, 0.0)) == (0.0, 0.0)


def test_similarity_from_distance_recovers_cosine_for_unit_vectors() -> None:
    assert similarity_from_distance(0.0) == pytest.approx(1.0)
    assert similarity_from_distance(math.sqrt(2.0)) == pytest.approx(0.0)


def test_replace_then_count_and_dimension(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:2], VECTORS[:2])
    assert store.count(STRATEGY_FIXED) == 2
    assert store.count(STRATEGY_HEADING) == 0
    assert store.dimension() == 2
    store.replace_strategy(STRATEGY_HEADING, CHUNKS[2:], VECTORS[2:])
    assert store.count(STRATEGY_HEADING) == 1
    store.close()


def test_replacing_a_strategy_drops_only_that_strategy(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:2], VECTORS[:2])
    store.replace_strategy(STRATEGY_HEADING, CHUNKS[2:], VECTORS[2:])
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:1], VECTORS[:1])
    assert store.count(STRATEGY_FIXED) == 1
    assert store.count(STRATEGY_HEADING) == 1
    store.close()


def test_search_ranks_by_cosine_and_returns_chunk_text(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:2], VECTORS[:2])
    hits = store.search(STRATEGY_FIXED, (1.0, 0.0), 2)
    assert [hit.chunk.text for hit in hits] == ["cancellation fee rule", "travel claim rule"]
    assert hits[0].similarity == pytest.approx(1.0)
    assert hits[1].similarity == pytest.approx(0.6)
    # The similarity is the true cosine of the (unnormalised) stored vector.
    assert hits[1].similarity == pytest.approx(pytest.approx(0.6, abs=1e-3))
    assert [hit.chunk.page for hit in hits] == [12, 13]
    store.close()


def test_search_is_a_true_cosine_against_the_domain_function(tmp_path: Path) -> None:
    from ndis.domain.pricing import cosine_similarity

    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:2], VECTORS[:2])
    query = (0.3, 0.4)
    hits = store.search(STRATEGY_FIXED, query, 2)
    expected = [cosine_similarity(query, vector) for vector in VECTORS[:2]]
    assert [hit.similarity for hit in hits] == pytest.approx(sorted(expected, reverse=True))
    store.close()


def test_search_on_empty_strategy_returns_empty(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    assert store.search(STRATEGY_FIXED, (1.0, 0.0), 3) == ()
    assert store.dimension() is None
    store.close()


def test_wrong_query_dimension_is_rejected(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:1], VECTORS[:1])
    with pytest.raises(ValueError, match="index dimension"):
        store.search(STRATEGY_FIXED, (1.0, 0.0, 0.0), 1)
    store.close()


def test_dimension_change_after_first_build_is_rejected(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:1], VECTORS[:1])
    with pytest.raises(ValueError, match="does not match"):
        store.replace_strategy(STRATEGY_HEADING, CHUNKS[2:], ((1.0, 0.0, 0.0),))
    store.close()


def test_replace_with_no_chunks_clears_the_strategy(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:1], VECTORS[:1])
    store.replace_strategy(STRATEGY_FIXED, [], [])
    assert store.count(STRATEGY_FIXED) == 0
    store.close()


def test_vectors_are_stored_unit_normalised(tmp_path: Path) -> None:
    store = SqliteVecRuleStore(tmp_path / "index.db")
    store.replace_strategy(STRATEGY_FIXED, CHUNKS[:1], VECTORS[:1])
    blob = store._connection.execute("SELECT chunk_vector FROM rule_vec_fixed").fetchone()[0]
    stored = struct.unpack("2f", blob)
    assert sum(value * value for value in stored) == pytest.approx(1.0)
    store.close()


def test_rule_index_missing_error_is_importable_and_raised_by_callers() -> None:
    """The error exists for the API layer; constructing it is enough here."""
    assert RuleIndexMissing("no index").args == ("no index",)


# --- Embedding cache -----------------------------------------------------------------------


def test_cache_key_depends_on_model_and_text() -> None:
    assert cache_key("m", "text one") == cache_key("m", "text one")
    assert cache_key("m", "text one") != cache_key("m", "text two")
    assert cache_key("m", "text one") != cache_key("other", "text one")


def test_cache_round_trip_and_misses(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "cache.db")
    assert cache.get("m", ("a", "b")) == (None, None)
    cache.put("m", ("a", "b"), ((1.0, 2.0), (3.0, 4.0)))
    assert cache.get("m", ("a", "b")) == ((1.0, 2.0), (3.0, 4.0))
    assert cache.get("m", ("a", "c")) == ((1.0, 2.0), None)
    assert cache.get("other", ("a",)) == (None,)
    assert cache.count() == 2
    cache.close()


def test_cache_put_is_idempotent_and_validates_lengths(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "cache.db")
    cache.put("m", ("a",), ((1.0, 2.0),))
    cache.put("m", ("a",), ((9.0, 9.0),))
    assert cache.get("m", ("a",)) == ((1.0, 2.0),)
    with pytest.raises(ValueError, match="one vector per text"):
        cache.put("m", ("a", "b"), ((1.0, 2.0),))
    cache.close()


def test_cache_survives_reopening(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path / "cache.db")
    cache.put("m", ("a",), ((1.0, 2.0, 3.0),))
    cache.close()
    reopened = EmbeddingCache(tmp_path / "cache.db")
    assert reopened.get("m", ("a",)) == ((1.0, 2.0, 3.0),)
    reopened.close()
