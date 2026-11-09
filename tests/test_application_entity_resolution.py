"""ResolveEntities use case against fake ports: lookup order, fuzzy scope, persistence, counts."""

from collections.abc import Iterable

from ndis.application.entity_resolution import ResolveEntities
from ndis.application.providers import Page, ProviderQuery
from ndis.domain.entities import Outlet, Provider
from ndis.domain.entity_resolution import (
    MapsPlace,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
    normalise_name,
)
from tests.conftest import SEED_PROVIDERS, make_place

ALPHA, BETA, GAMMA, DELTA_REVOKED, EPSILON = SEED_PROVIDERS


class FakeProviders:
    def __init__(self, providers: list[Provider]) -> None:
        self.providers = providers
        self.domain_lookups: list[str] = []
        self.name_lookups: list[str] = []

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        self.providers = list(providers)

    def find(self, query: ProviderQuery) -> Page[Provider]:
        return Page(tuple(self.providers), len(self.providers), query.limit, query.offset)

    def find_by_website_domain(self, domain: str) -> tuple[Provider, ...]:
        self.domain_lookups.append(domain)
        return tuple(p for p in self.providers if p.website_domain == domain)

    def find_by_normalised_name(self, name: str) -> tuple[Provider, ...]:
        self.name_lookups.append(name)
        return tuple(
            p
            for p in self.providers
            if name in {normalise_name(p.business_name), normalise_name(p.legal_name)}
        )

    def brisbane_providers(self) -> tuple[Provider, ...]:
        return tuple(p for p in self.providers if p.brisbane_area)


class FakePlaces:
    def __init__(self, places: list[MapsPlace]) -> None:
        self._places = places

    def places(self) -> tuple[MapsPlace, ...]:
        return tuple(self._places)


class FakeLinks:
    def __init__(self) -> None:
        self.stored: list[ProviderLink] = []

    def replace_all(self, links: Iterable[ProviderLink]) -> None:
        self.stored = list(links)

    def all(self) -> tuple[ProviderLink, ...]:
        return tuple(self.stored)


class RecordingScorer:
    """Scores 100 when the query is a prefix of the candidate and records every comparison."""

    def __init__(self) -> None:
        self.compared: list[tuple[str, str]] = []

    def score(self, query: str, candidate: str) -> float:
        self.compared.append((query, candidate))
        return 100.0 if candidate.startswith(query) else 10.0


PLACES = [
    make_place("p-domain", "Unrelated Title", website="https://www.10000000001.example.com.au/x"),
    make_place("p-head", "Beta Supports - NDIS Provider Brisbane", website="https://beta.example"),
    make_place("p-revoked", "DELTA REVOKED"),
    make_place("p-category", "Hire Co", category="Recruitment Agency"),
    make_place("p-fuzzy", "Epsilon Nationa", website="https://eps.example"),
    make_place("p-none", "Nobody Known", website="https://nobody.example"),
]


def test_use_case_resolves_in_order_and_persists() -> None:
    providers, links, scorer = FakeProviders(list(SEED_PROVIDERS)), FakeLinks(), RecordingScorer()
    result = ResolveEntities(FakePlaces(PLACES), providers, links, scorer)()

    by_place = {link.place.place_id: link.match for link in result.links}
    assert (by_place["p-domain"].strategy, by_place["p-domain"].abn) == (
        MatchStrategy.DOMAIN,
        ALPHA.abn,
    )
    assert (by_place["p-head"].strategy, by_place["p-head"].abn) == (MatchStrategy.NAME, BETA.abn)
    assert by_place["p-revoked"].match_status is MatchStatus.REVOKED_OR_BANNED
    assert by_place["p-category"].match_status is MatchStatus.NOT_A_PROVIDER
    assert (by_place["p-fuzzy"].strategy, by_place["p-fuzzy"].abn) == (
        MatchStrategy.FUZZY,
        EPSILON.abn,
    )
    assert by_place["p-none"].match_status is MatchStatus.UNREGISTERED

    assert links.stored == list(result.links)
    assert result.by_status == {
        MatchStatus.REGISTERED: 3,
        MatchStatus.UNREGISTERED: 1,
        MatchStatus.REVOKED_OR_BANNED: 1,
        MatchStatus.AMBIGUOUS: 0,
        MatchStatus.NOT_A_PROVIDER: 1,
    }
    assert result.by_strategy == {
        MatchStrategy.DOMAIN: 1,
        MatchStrategy.NAME: 2,
        MatchStrategy.FUZZY: 1,
        MatchStrategy.CATEGORY: 1,
        MatchStrategy.NONE: 1,
    }
    assert result.summary().startswith("6 places; status: ambiguous=0, not_a_provider=1")


def test_fuzzy_runs_only_when_needed_and_only_over_brisbane_names() -> None:
    providers, scorer = FakeProviders(list(SEED_PROVIDERS)), RecordingScorer()
    ResolveEntities(FakePlaces(PLACES), providers, FakeLinks(), scorer)()
    queries = {query for query, _ in scorer.compared}
    assert queries == {"epsilon nationa", "nobody known"}
    candidates = {candidate for _, candidate in scorer.compared}
    assert normalise_name(GAMMA.business_name) not in candidates
    assert candidates == {
        normalise_name(p.business_name) for p in SEED_PROVIDERS if p.brisbane_area
    }
    # Domain lookups are skipped for places without a website; name lookups use the head too.
    assert "10000000001.example.com.au" in providers.domain_lookups
    assert "beta supports" in providers.name_lookups
    assert "beta supports ndis provider brisbane" in providers.name_lookups
