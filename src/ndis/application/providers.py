"""Provider repository port, query types and the list-providers use case."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ndis.domain.entities import Outlet, Provider
from ndis.domain.geo import (
    Coordinates,
    Distance,
    PostcodeCentroid,
    distance_from,
    nearest_to,
    rank_by_distance,
)
from ndis.domain.register import REGISTRATION_STATUSES

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True)
class ProviderQuery:
    """Filters and pagination for listing providers.

    ``state`` and ``postcode`` match the head office or any outlet of the provider;
    ``group`` matches a registration group name exactly; ``status`` is one of the register's
    statuses (matched case-insensitively); ``brisbane_area`` filters on the provider flag.
    ``near`` is an origin postcode: when set, results are ranked by distance from its
    centroid and ``max_km`` (inclusive, requires ``near``) drops providers further away or
    without a measurable distance.
    """

    state: str | None = None
    postcode: str | None = None
    group: str | None = None
    status: str | None = None
    brisbane_area: bool | None = None
    near: str | None = None
    max_km: float | None = None
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


@dataclass(frozen=True)
class Page[T]:
    """One page of results plus the total number of matches."""

    items: tuple[T, ...]
    total: int
    limit: int
    offset: int


@dataclass(frozen=True)
class ProviderLocation:
    """Where a matched provider's head office is, for distance ranking."""

    abn: str
    postcode: str | None


@dataclass(frozen=True)
class RankedProvider:
    """A provider in a result page with its distance from the origin, if one was asked for."""

    provider: Provider
    distance: Distance | None


@dataclass(frozen=True)
class ProviderPage(Page[RankedProvider]):
    """A page of providers plus the origin postcode distances were measured from."""

    distance_origin: str | None = None


class PostcodeCentroids(Protocol):
    """Persistence port for postcode centroids."""

    def replace_all(self, centroids: Iterable[PostcodeCentroid]) -> None:
        """Replace every stored centroid with the given ones atomically."""
        ...

    def coordinates_for(self, postcodes: Iterable[str]) -> Mapping[str, Coordinates]:
        """Centroid coordinates keyed by postcode, for the postcodes that are known."""
        ...


class ProviderRepository(Protocol):
    """Persistence port for providers and their outlets."""

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        """Replace every stored provider, group and outlet with the given ones atomically."""
        ...

    def find(self, query: ProviderQuery) -> Page[Provider]:
        """Return the providers matching ``query`` ordered by business name then ABN."""
        ...

    def matched_locations(self, query: ProviderQuery) -> tuple[ProviderLocation, ...]:
        """ABN and head-office postcode of every provider matching ``query``'s filters,
        ordered by business name then ABN, ignoring ``limit``/``offset``."""
        ...

    def exact_coordinates(self) -> Mapping[str, tuple[Coordinates, ...]]:
        """Maps listing coordinates per ABN for every ``registered`` provider link."""
        ...

    def find_by_abns(self, abns: Sequence[str]) -> tuple[Provider, ...]:
        """The providers with the given ABNs, in the order given."""
        ...

    def find_by_website_domain(self, domain: str) -> tuple[Provider, ...]:
        """Providers whose stored website domain equals ``domain`` exactly, ordered by ABN."""
        ...

    def find_by_normalised_name(self, name: str) -> tuple[Provider, ...]:
        """Providers whose normalised business or legal name equals ``name``, ordered by ABN."""
        ...

    def brisbane_providers(self) -> tuple[Provider, ...]:
        """Every provider flagged ``brisbane_area``, ordered by ABN."""
        ...


class InvalidQueryError(ValueError):
    """A query with out-of-range pagination or unusable filter values."""


class UnknownPostcodeError(InvalidQueryError):
    """The ``near`` postcode has no centroid, so distances cannot be measured from it."""

    def __init__(self, postcode: str) -> None:
        super().__init__(f"postcode {postcode} is not a known Australian postcode")
        self.postcode = postcode


def normalise_query(query: ProviderQuery) -> ProviderQuery:
    """Validate pagination and canonicalise filter values (upper-case state, trimmed text)."""
    if not 1 <= query.limit <= MAX_PAGE_SIZE:
        raise InvalidQueryError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if query.offset < 0:
        raise InvalidQueryError("offset must not be negative")
    postcode = query.postcode.strip() if query.postcode else None
    if postcode is not None and not (postcode.isdigit() and len(postcode) == 4):
        raise InvalidQueryError("postcode must be four digits")
    near = query.near.strip() if query.near else None
    if near is not None and not (near.isdigit() and len(near) == 4):
        raise InvalidQueryError("near must be a four-digit postcode")
    if query.max_km is not None:
        if near is None:
            raise InvalidQueryError("max_km requires near")
        if not query.max_km > 0:
            raise InvalidQueryError("max_km must be greater than zero")
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
        near=near,
        max_km=query.max_km,
        limit=query.limit,
        offset=query.offset,
    )


class ListProviders:
    """Use case: list registered providers matching filters, one page at a time.

    Without ``near`` the repository orders and paginates. With ``near`` the whole matched
    set is ranked by distance here before the page is cut: each provider is measured from
    the nearest of its ``registered`` Maps listings when it has any (``exact``), from its
    head-office postcode centroid otherwise (``postcode_centroid``), and left unmeasured
    when it has neither. ``centroids`` may be ``None`` when no postcode table is loaded, in
    which case every ``near`` query is an ``UnknownPostcodeError``.
    """

    def __init__(
        self, repository: ProviderRepository, centroids: PostcodeCentroids | None = None
    ) -> None:
        self._repository = repository
        self._centroids = centroids

    def __call__(self, query: ProviderQuery) -> ProviderPage:
        query = normalise_query(query)
        if query.near is None:
            page = self._repository.find(query)
            return ProviderPage(
                items=tuple(RankedProvider(provider, None) for provider in page.items),
                total=page.total,
                limit=page.limit,
                offset=page.offset,
            )
        return self._ranked(query, query.near)

    def _ranked(self, query: ProviderQuery, near: str) -> ProviderPage:
        origin = self._centroids.coordinates_for([near]).get(near) if self._centroids else None
        if origin is None:
            raise UnknownPostcodeError(near)
        locations = self._repository.matched_locations(query)
        postcodes = {location.postcode for location in locations if location.postcode}
        centroids = self._centroids.coordinates_for(postcodes) if self._centroids else {}
        exact = self._repository.exact_coordinates()
        measured = [
            (
                location.abn,
                distance_from(
                    origin,
                    nearest_to(origin, exact.get(location.abn, ())),
                    centroids.get(location.postcode) if location.postcode else None,
                ),
            )
            for location in locations
        ]
        ranked = rank_by_distance(measured, query.max_km)
        page = ranked[query.offset : query.offset + query.limit]
        providers = self._repository.find_by_abns([abn for abn, _ in page])
        return ProviderPage(
            items=tuple(
                RankedProvider(provider, distance)
                for provider, (_, distance) in zip(providers, page, strict=True)
            ),
            total=len(ranked),
            limit=query.limit,
            offset=query.offset,
            distance_origin=near,
        )
