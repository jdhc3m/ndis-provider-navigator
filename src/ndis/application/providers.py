"""Provider repository port, query types and the list-providers use case."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ndis.domain.entities import Outlet, Provider
from ndis.domain.register import REGISTRATION_STATUSES

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True)
class ProviderQuery:
    """Filters and pagination for listing providers.

    ``state`` and ``postcode`` match the head office or any outlet of the provider;
    ``group`` matches a registration group name exactly; ``status`` is one of the register's
    statuses (matched case-insensitively); ``brisbane_area`` filters on the provider flag.
    """

    state: str | None = None
    postcode: str | None = None
    group: str | None = None
    status: str | None = None
    brisbane_area: bool | None = None
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


@dataclass(frozen=True)
class Page[T]:
    """One page of results plus the total number of matches."""

    items: tuple[T, ...]
    total: int
    limit: int
    offset: int


class ProviderRepository(Protocol):
    """Persistence port for providers and their outlets."""

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        """Replace every stored provider, group and outlet with the given ones atomically."""
        ...

    def find(self, query: ProviderQuery) -> Page[Provider]:
        """Return the providers matching ``query`` ordered by business name then ABN."""
        ...


class InvalidQueryError(ValueError):
    """A query with out-of-range pagination or unusable filter values."""


def normalise_query(query: ProviderQuery) -> ProviderQuery:
    """Validate pagination and canonicalise filter values (upper-case state, trimmed text)."""
    if not 1 <= query.limit <= MAX_PAGE_SIZE:
        raise InvalidQueryError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if query.offset < 0:
        raise InvalidQueryError("offset must not be negative")
    postcode = query.postcode.strip() if query.postcode else None
    if postcode is not None and not (postcode.isdigit() and len(postcode) == 4):
        raise InvalidQueryError("postcode must be four digits")
    status = query.status.strip() if query.status and query.status.strip() else None
    if status is not None:
        known = {value.lower(): value for value in REGISTRATION_STATUSES}
        if status.lower() not in known:
            raise InvalidQueryError(f"status must be one of {sorted(REGISTRATION_STATUSES)}")
        status = known[status.lower()]
    return ProviderQuery(
        state=query.state.strip().upper() if query.state and query.state.strip() else None,
        postcode=postcode,
        group=query.group.strip() if query.group and query.group.strip() else None,
        status=status,
        brisbane_area=query.brisbane_area,
        limit=query.limit,
        offset=query.offset,
    )


class ListProviders:
    """Use case: list registered providers matching filters, one page at a time."""

    def __init__(self, repository: ProviderRepository) -> None:
        self._repository = repository

    def __call__(self, query: ProviderQuery) -> Page[Provider]:
        return self._repository.find(normalise_query(query))
