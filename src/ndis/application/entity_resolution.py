"""Ports and the use case that links Maps places to register providers."""

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ndis.application.providers import ProviderRepository
from ndis.domain.entities import Provider
from ndis.domain.entity_resolution import (
    FUZZY_THRESHOLD,
    EntityMatch,
    MapsPlace,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
    is_generic_domain,
    name_keys,
    normalise_name,
    resolve_place,
    title_head,
    title_keys,
)


class MapsPlaceSource(Protocol):
    """Read port for the private Maps export; yields only the public-safe fields."""

    def places(self) -> tuple[MapsPlace, ...]:
        """Every place in the export, in a stable order."""
        ...


class ProviderLinkRepository(Protocol):
    """Persistence port for the resolved links."""

    def replace_all(self, links: Iterable[ProviderLink]) -> None:
        """Replace every stored link with the given ones atomically."""
        ...

    def all(self) -> tuple[ProviderLink, ...]:
        """Every stored link ordered by place id."""
        ...


class FuzzyScorer(Protocol):
    """Similarity of two normalised names on a 0–100 scale (100 is identical)."""

    def score(self, query: str, candidate: str) -> float:
        """Score ``candidate`` against ``query``; both are already normalised names."""
        ...


@dataclass(frozen=True)
class ResolutionResult:
    """All links plus the counts the report and the log print."""

    links: tuple[ProviderLink, ...]
    by_status: Mapping[MatchStatus, int]
    by_strategy: Mapping[MatchStrategy, int]

    def summary(self) -> str:
        """One log line: total places, then the counts per status and per strategy."""
        statuses = ", ".join(f"{s.value}={n}" for s, n in sorted(self.by_status.items()))
        strategies = ", ".join(f"{s.value}={n}" for s, n in sorted(self.by_strategy.items()))
        return f"{len(self.links)} places; status: {statuses}; strategy: {strategies}"


class ResolveEntities:
    """Use case: resolve every place in the export and persist the links.

    Fuzzy scoring compares the normalised title head with every normalised name of the
    Brisbane-area providers only; the national register is far too large for a fuzzy pass and
    a non-Brisbane fuzzy hit for a Brisbane shopfront is almost always a different business.
    """

    def __init__(
        self,
        places: MapsPlaceSource,
        providers: ProviderRepository,
        links: ProviderLinkRepository,
        scorer: FuzzyScorer,
        threshold: float = FUZZY_THRESHOLD,
    ) -> None:
        self._places = places
        self._providers = providers
        self._links = links
        self._scorer = scorer
        self._threshold = threshold

    def __call__(self) -> ResolutionResult:
        brisbane = [
            (provider, name_keys(provider)) for provider in self._providers.brisbane_providers()
        ]
        links: list[ProviderLink] = []
        for place in self._places.places():
            links.append(ProviderLink(place, self._resolve(place, brisbane)))
        self._links.replace_all(links)
        by_status = Counter(link.match.match_status for link in links)
        by_strategy = Counter(link.match.strategy for link in links)
        return ResolutionResult(
            tuple(links),
            {status: by_status[status] for status in MatchStatus},
            {strategy: by_strategy[strategy] for strategy in MatchStrategy},
        )

    def _resolve(
        self, place: MapsPlace, brisbane: list[tuple[Provider, frozenset[str]]]
    ) -> EntityMatch:
        domain_candidates: tuple[Provider, ...] = ()
        if place.website_domain and not is_generic_domain(place.website_domain):
            domain_candidates = self._providers.find_by_website_domain(place.website_domain)
        name_candidates = tuple(
            provider
            for key in sorted(title_keys(place.title))
            for provider in self._providers.find_by_normalised_name(key)
        )
        head = normalise_name(title_head(place.title))

        def fuzzy() -> list[tuple[Provider, float]]:
            if not head:
                return []
            return [
                (provider, max(self._scorer.score(head, key) for key in keys))
                for provider, keys in brisbane
                if keys
            ]

        return resolve_place(place, domain_candidates, name_candidates, fuzzy, self._threshold)
