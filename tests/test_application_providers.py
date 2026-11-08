"""List-providers use case and query normalisation against a fake repository."""

from collections.abc import Iterable

import pytest

from ndis.application.providers import (
    MAX_PAGE_SIZE,
    InvalidQueryError,
    ListProviders,
    Page,
    ProviderQuery,
    normalise_query,
)
from ndis.domain.entities import Outlet, Provider
from tests.conftest import SEED_PROVIDERS


class FakeRepository:
    def __init__(self) -> None:
        self.queries: list[ProviderQuery] = []
        self.providers: list[Provider] = []

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        self.providers = list(providers)

    def find(self, query: ProviderQuery) -> Page[Provider]:
        self.queries.append(query)
        items = tuple(self.providers[query.offset : query.offset + query.limit])
        return Page(items=items, total=len(self.providers), limit=query.limit, offset=query.offset)


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
    assert [provider.abn for provider in page.items] == ["10000000002", "10000000003"]
    assert (page.limit, page.offset) == (2, 1)


def test_blank_filters_become_none() -> None:
    query = normalise_query(ProviderQuery(state="", postcode="", group=" ", status="", limit=5))
    assert query == ProviderQuery(limit=5)
    assert normalise_query(ProviderQuery(status="Revoked", brisbane_area=False)).status == "Revoked"


@pytest.mark.parametrize(
    "query",
    [
        ProviderQuery(limit=0),
        ProviderQuery(limit=MAX_PAGE_SIZE + 1),
        ProviderQuery(offset=-1),
        ProviderQuery(postcode="41O1"),
        ProviderQuery(postcode="12345"),
    ],
)
def test_invalid_queries_are_rejected(query: ProviderQuery) -> None:
    with pytest.raises(InvalidQueryError):
        normalise_query(query)
