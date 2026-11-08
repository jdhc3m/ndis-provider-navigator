"""Composition root: environment-driven wiring of adapters into use cases."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends

from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository, create_sqlite_engine
from ndis.application.providers import ListProviders, ProviderRepository

DB_PATH_ENV = "NDIS_DB_PATH"
DEFAULT_DB_PATH = Path("data/ndis.db")


def database_path() -> Path:
    """SQLite file from ``NDIS_DB_PATH``, defaulting to ``data/ndis.db``."""
    return Path(os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH)


@lru_cache(maxsize=8)
def provider_repository_for(path: Path) -> SqlAlchemyProviderRepository:
    """One repository (and engine) per database file for the life of the process."""
    return SqlAlchemyProviderRepository(create_sqlite_engine(path))


def provider_repository() -> ProviderRepository:
    return provider_repository_for(database_path().resolve())


def list_providers_use_case(
    repository: Annotated[ProviderRepository, Depends(provider_repository)],
) -> ListProviders:
    return ListProviders(repository)
