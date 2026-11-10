"""List-providers use case and query normalisation against a fake repository."""

from collections.abc import Iterable, Mapping, Sequence

import pytest

from ndis.application.providers import (
    MAX_PAGE_SIZE,
    InvalidQueryError,
    ListProviders,
    Page,
    ProviderLocation,
    ProviderQuery,
    UnknownPostcodeError,
    normalise_query,
)
from ndis.domain.entities import Outlet, Provider
from ndis.domain.geo import Coordinates, DistanceSource, PostcodeCentroid
from tests.conftest import SEED_CENTROIDS, SEED_PROVIDERS, make_provider


class FakeRepository:
    def __init__(self, exact: Mapping[str, tuple[Coordinates, ...]] | None = None) -> None:
        self.queries: list[ProviderQuery] = []
        self.providers: list[Provider] = []
        self.exact = dict(exact or {})

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        self.providers = list(providers)

    def find(self, query: ProviderQuery) -> Page[Provider]:
        self.queries.append(query)
        items = tuple(self.providers[query.offset : query.offset + query.limit])
        return Page(items=items, total=len(self.providers), limit=query.limit, offset=query.offset)

    def matched_locations(self, query: ProviderQuery) -> tuple[ProviderLocation, ...]:
        self.queries.append(query)
        return tuple(ProviderLocation(p.abn, p.head_office.postcode) for p in self.providers)

    def exact_coordinates(self) -> Mapping[str, tuple[Coordinates, ...]]:
        return self.exact

    def find_by_abns(self, abns: Sequence[str]) -> tuple[Provider, ...]:
        by_abn = {provider.abn: provider for provider in self.providers}
        return tuple(by_abn[abn] for abn in abns if abn in by_abn)


class FakeCentroids:
    def __init__(self, centroids: Iterable[PostcodeCentroid]) -> None:
        self.centroids = {c.postcode: c.coordinates for c in centroids}

    def replace_all(self, centroids: Iterable[PostcodeCentroid]) -> None:
        self.centroids = {c.postcode: c.coordinates for c in centroids}

    def coordinates_for(self, postcodes: Iterable[str]) -> Mapping[str, Coordinates]:
        return {p: self.centroids[p] for p in postcodes if p in self.centroids}


def test_use_case_normalises_and_delegates() -> None:
    repository = FakeRepository()
    repository.replace_all(SEED_PROVIDERS, [])
    page = ListProviders(repository)(
        ProviderQuery(state=" qld ", postcode=" 4101", group=" Plan Management ", limit=2, offset=1)
    )
    assert repository.queries == [
        ProviderQuery(state="QLD", postcode="4101", group="Plan Management", limit=2, offset=1)
    ]
    assert page.total == len(SEED_PROVIDERS)
    assert [ranked.provider.abn for ranked in page.items] == ["10000000002", "10000000003"]
    assert all(ranked.distance is None for ranked in page.items)
    assert page.distance_origin is None
    assert (page.limit, page.offset) == (2, 1)


# Alpha has two registered Maps rows: one in West End and one in Cairns; the nearer wins.
EXACT = {
    "10000000001": (Coordinates(-16.923761, 145.742303), Coordinates(-27.48, 153.01)),
}
NO_POSTCODE = make_provider("10000000006", "Zeta Unlocated", state=None, postcode=None)


def test_near_ranks_the_whole_matched_set_before_paginating() -> None:
    repository = FakeRepository(EXACT)
    repository.replace_all([*SEED_PROVIDERS, NO_POSTCODE], [])
    use_case = ListProviders(repository, FakeCentroids(SEED_CENTROIDS))
    page = use_case(ProviderQuery(near=" 4101 ", limit=3))
    assert page.distance_origin == "4101"
    assert page.total == 6
    assert repository.queries == [ProviderQuery(near="4101", limit=3)]
    assert [(r.provider.abn, r.distance and r.distance.source) for r in page.items] == [
        ("10000000004", DistanceSource.POSTCODE_CENTROID),  # 4101 itself: 0 km
        ("10000000001", DistanceSource.EXACT),  # West End row, not the Cairns one
        ("10000000002", DistanceSource.POSTCODE_CENTROID),  # 4000 centroid
    ]
    assert page.items[1].distance is not None
    assert page.items[1].distance.km == pytest.approx(0.474, abs=0.001)
    # The unlocated provider is last, after the interstate ones, with no distance.
    tail = use_case(ProviderQuery(near="4101", limit=3, offset=3))
    assert [r.provider.abn for r in tail.items] == ["10000000003", "10000000005", "10000000006"]
    assert tail.items[-1].distance is None


def test_max_km_drops_far_and_unmeasured_providers_from_the_total() -> None:
    repository = FakeRepository(EXACT)
    repository.replace_all([*SEED_PROVIDERS, NO_POSTCODE], [])
    use_case = ListProviders(repository, FakeCentroids(SEED_CENTROIDS))
    page = use_case(ProviderQuery(near="4101", max_km=2.5))
    assert page.total == 3
    assert [r.provider.abn for r in page.items] == ["10000000004", "10000000001", "10000000002"]
    assert all(r.distance is not None and r.distance.km <= 2.5 for r in page.items)


def test_a_provider_that_vanishes_between_the_two_reads_is_skipped() -> None:
    repository = FakeRepository(EXACT)
    repository.replace_all(SEED_PROVIDERS, [])
    locations = repository.matched_locations(ProviderQuery(near="4101"))
    # The register is reloaded after the ranking read: 4101's provider is gone.
    repository.matched_locations = lambda query: locations  # type: ignore[method-assign]
    repository.providers = [p for p in SEED_PROVIDERS if p.abn != "10000000004"]
    page = ListProviders(repository, FakeCentroids(SEED_CENTROIDS))(
        ProviderQuery(near="4101", limit=2)
    )
    assert page.total == 5
    assert [r.provider.abn for r in page.items] == ["10000000001"]


def test_unknown_or_unavailable_origin_is_an_error() -> None:
    repository = FakeRepository()
    repository.replace_all(SEED_PROVIDERS, [])
    with pytest.raises(UnknownPostcodeError, match="9999 is not a known Australian postcode"):
        ListProviders(repository, FakeCentroids(SEED_CENTROIDS))(ProviderQuery(near="9999"))
    with pytest.raises(UnknownPostcodeError) as info:
        ListProviders(repository)(ProviderQuery(near="4101"))
    assert info.value.postcode == "4101"
    assert isinstance(info.value, InvalidQueryError)
    assert repository.queries == []


def test_blank_filters_become_none() -> None:
    query = normalise_query(ProviderQuery(state="", postcode="", group=" ", status="", limit=5))
    assert query == ProviderQuery(limit=5)
    assert normalise_query(ProviderQuery(status="Revoked", brisbane_area=False)).status == "Revoked"


@pytest.mark.parametrize("status", ["approved", "APPROVED", " Approved ", "revoked"])
def test_status_is_canonicalised_case_insensitively(status: str) -> None:
    assert normalise_query(ProviderQuery(status=status)).status == status.strip().capitalize()


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(InvalidQueryError, match="status must be one of"):
        normalise_query(ProviderQuery(status="Pending"))


@pytest.mark.parametrize(
    "query",
    [
        ProviderQuery(limit=0),
        ProviderQuery(limit=MAX_PAGE_SIZE + 1),
        ProviderQuery(offset=-1),
        ProviderQuery(postcode="41O1"),
        ProviderQuery(postcode="12345"),
        ProviderQuery(near="41O1"),
        ProviderQuery(near="123"),
        ProviderQuery(max_km=5.0),
        ProviderQuery(near="4101", max_km=0.0),
        ProviderQuery(near="4101", max_km=-1.0),
    ],
)
def test_invalid_queries_are_rejected(query: ProviderQuery) -> None:
    with pytest.raises(InvalidQueryError):
        normalise_query(query)
