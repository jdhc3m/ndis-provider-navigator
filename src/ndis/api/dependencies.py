"""Composition root: environment-driven wiring of adapters into use cases."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import Engine

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.adapters.tracing import traced
from ndis.application.ask import Ask, ClaimEvidenceSource, MissingIndexRuleAnswerer, RuleAnswerer
from ndis.application.catalogue import GetPriceLimit, SupportItemRepository
from ndis.application.extraction import TokenBudget
from ndis.application.pricing import AskRule, EmbeddingClient
from ndis.application.providers import ListProviders, ProviderRepository
from ndis.config import (
    embed_model,
    llm_api_key,
    llm_base_url,
    llm_model,
    rule_chunk_strategy,
    rule_index_path,
    rule_similarity_threshold,
    rule_top_k,
)

DB_PATH_ENV = "NDIS_DB_PATH"
DEFAULT_DB_PATH = Path("data/ndis.db")
RULE_INDEX_PATH_ENV = "RULE_INDEX_PATH"
# Token budget for one question: one embedding call plus one judge call over a handful
# of 600-token chunks never comes near this.
QUESTION_BUDGET = 200_000
EMBEDDING_CACHE_ENV = "EMBEDDING_CACHE_PATH"
DEFAULT_EMBEDDING_CACHE = Path("data/private/embedding_cache.db")
EXTRACTION_DIR_ENV = "EXTRACTION_DIR"
DEFAULT_EXTRACTION_DIR = Path("data/private/extraction")
PAGES_DIR_ENV = "PAGES_DIR"
DEFAULT_PAGES_DIR = Path("data/private/pages")


def database_path() -> Path:
    """SQLite file from ``NDIS_DB_PATH``, defaulting to ``data/ndis.db``."""
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


@lru_cache(maxsize=8)
def engine_for(path: Path) -> Engine:
    """One engine per database file for the life of the process, shared by all repositories."""
    return create_sqlite_engine(path)


@lru_cache(maxsize=8)
def provider_repository_for(path: Path) -> SqlAlchemyProviderRepository:
    return SqlAlchemyProviderRepository(engine_for(path))


@lru_cache(maxsize=8)
def support_item_repository_for(path: Path) -> SqlAlchemySupportItemRepository:
    return SqlAlchemySupportItemRepository(engine_for(path))


def provider_repository() -> ProviderRepository:
    return provider_repository_for(database_path().resolve())


def support_item_repository() -> SupportItemRepository:
    return support_item_repository_for(database_path().resolve())


def list_providers_use_case(
    repository: Annotated[ProviderRepository, Depends(provider_repository)],
) -> ListProviders:
    return ListProviders(repository)


def get_price_limit_use_case(
    repository: Annotated[SupportItemRepository, Depends(support_item_repository)],
) -> GetPriceLimit:
    return GetPriceLimit(repository)


# --- Pricing rules RAG ----------------------------------------------------------------------


def rule_index_database_path() -> Path:
    """The sqlite-vec index file from ``RULE_INDEX_PATH``, defaulting to
    ``data/rule_index.db``."""
    return Path(os.environ.get(RULE_INDEX_PATH_ENV) or rule_index_path())


@lru_cache(maxsize=8)
def rule_store_for(path: Path) -> SqliteVecRuleStore:
    return SqliteVecRuleStore(path)


def rule_store() -> SqliteVecRuleStore:
    path = rule_index_database_path()
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="rule index not built yet; run scripts/build_rule_index.py",
        )
    return rule_store_for(path)


@lru_cache(maxsize=8)
def embedding_cache_for(path: Path) -> EmbeddingCache:
    return EmbeddingCache(path)


def rule_embedding_cache() -> EmbeddingCache:
    return embedding_cache_for(Path(os.environ.get(EMBEDDING_CACHE_ENV) or DEFAULT_EMBEDDING_CACHE))


def question_embedding_client() -> EmbeddingClient:
    """The bounded embedding client for one question's own embedding call."""
    if not embed_model():
        raise HTTPException(status_code=503, detail="embedding model not configured")
    return OpenAICompatibleClient(
        base_url=llm_base_url(),
        api_key=llm_api_key(),
        model=llm_model(),
        embed_model=embed_model(),
        budget=TokenBudget(QUESTION_BUDGET),
    )


def question_llm_client() -> OpenAICompatibleClient:
    """The bounded completion client for one question's judge call."""
    if not llm_model():
        raise HTTPException(status_code=503, detail="LLM not configured")
    return OpenAICompatibleClient(
        base_url=llm_base_url(),
        api_key=llm_api_key(),
        model=llm_model(),
        budget=TokenBudget(QUESTION_BUDGET),
    )


def ask_rule_use_case(
    store: Annotated[SqliteVecRuleStore, Depends(rule_store)],
) -> AskRule:
    """One use case per question; the embedding cache is shared, the LLM clients and
    their token budgets are fresh per request."""
    return rule_answerer_for(store)


# --- Combined answers (POST /ask) ------------------------------------------------------------


def extraction_dir() -> Path:
    """The private extraction-cache directory from ``EXTRACTION_DIR``."""
    return Path(os.environ.get(EXTRACTION_DIR_ENV) or DEFAULT_EXTRACTION_DIR)


def pages_dir() -> Path:
    """The private pages directory from ``PAGES_DIR``."""
    return Path(os.environ.get(PAGES_DIR_ENV) or DEFAULT_PAGES_DIR)


def claim_evidence_source() -> ClaimEvidenceSource:
    """Evidence over the extraction cache and stored pages of the main database."""
    return ExtractionEvidenceSource(
        engine_for(database_path().resolve()), extraction_dir(), pages_dir()
    )


def rule_answerer_for(store: SqliteVecRuleStore | None) -> RuleAnswerer:
    """The ticket 06 pipeline over the given store, or a refusing stand-in when the
    index file is missing (a combined answer degrades, it does not fail)."""
    if store is None:
        return MissingIndexRuleAnswerer()
    embedder: EmbeddingClient = traced(
        CachedEmbeddingClient(question_embedding_client(), rule_embedding_cache(), embed_model()),
        "rule_embedding",
    )
    return AskRule(
        embedder,
        store,
        traced(question_llm_client(), "rule_judge"),
        strategy=rule_chunk_strategy(),
        threshold=rule_similarity_threshold(),
        top_k=rule_top_k(),
    )


def ask_use_case(
    providers: Annotated[ListProviders, Depends(list_providers_use_case)],
    catalogue: Annotated[GetPriceLimit, Depends(get_price_limit_use_case)],
    evidence: Annotated[ClaimEvidenceSource, Depends(claim_evidence_source)],
) -> Ask:
    """One use case per question; the intent-parse client and its token budget are
    fresh per request, the rule answerer degrades to refusals without an index."""
    return Ask(
        traced(question_llm_client(), "intent_parse"),
        providers,
        catalogue,
        rule_answerer_for(rule_store_maybe()),
        evidence,
    )


def rule_store_maybe() -> SqliteVecRuleStore | None:
    """The rule index when built, else ``None`` — unlike ``rule_store``, this never
    raises, because a /ask question that needs no rules must still be answerable."""
    path = rule_index_database_path()
    return rule_store_for(path) if path.exists() else None
