"""Composition root: environment-driven wiring of adapters into use cases."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine

from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.catalogue import GetPriceLimit, SupportItemRepository
from ndis.application.providers import ListProviders, ProviderRepository

DB_PATH_ENV = "NDIS_DB_PATH"
DEFAULT_DB_PATH = Path("data/ndis.db")


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
