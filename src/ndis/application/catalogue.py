"""Support item repository port, price-limit query types, the get-price-limit and
service-search use cases."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ndis.application.providers import InvalidQueryError
from ndis.domain.catalogue import FINANCIAL_YEAR, price_limit
from ndis.domain.entities import Remoteness, SupportItem
from ndis.domain.intent import MAX_SERVICE_CHARS
from ndis.domain.register import STATES
from ndis.domain.service_search import search_service_items


@dataclass(frozen=True)
class PriceLimitQuery:
    """Which support item, state and remoteness tier to price.

    ``remoteness`` is one of the ``Remoteness`` values (matched case-insensitively) and
    defaults to the national, non-remote tier when blank. ``state`` is ``None`` only when
    a question stated none: the national tier applies and the result says so. The
    ``/price-limits`` endpoint always requires a state and is unaffected.
    """

    item: str
    state: str | None
    remoteness: str | None = None


@dataclass(frozen=True)
class PriceLimitResult:
    """The item, the normalised query and the limit that applies (``None`` when unpublished)."""

    item: SupportItem
    state: str | None
    remoteness: Remoteness
    limit: Decimal | None


class SupportItemRepository(Protocol):
    """Persistence port for the support catalogue."""

    def replace_all(self, items: Iterable[SupportItem]) -> None:
        """Replace every stored support item with the given ones atomically."""
        ...

    def get(self, item_number: str) -> SupportItem | None:
        """Return the support item with this exact number, or ``None``."""
        ...

    def all_items(self) -> tuple[SupportItem, ...]:
        """Every stored support item, so the service search can match over them in code."""
        ...


class UnknownSupportItemError(LookupError):
    """The requested support item number is not in the loaded catalogue."""


def normalise_price_query(query: PriceLimitQuery) -> tuple[str, str | None, Remoteness]:
    """Validate and canonicalise the query: trimmed item, upper-case state (``None``
    stays ``None`` — the national tier), remoteness enum."""
    item = query.item.strip()
    if not item:
        raise InvalidQueryError("item must not be blank")
    if query.state is None:
        state = None
    else:
        state = query.state.strip().upper()
        if state not in STATES:
            raise InvalidQueryError(f"state must be one of {sorted(STATES)}")
    raw = (query.remoteness or "").strip().lower()
    if not raw:
        return item, state, Remoteness.NATIONAL
    try:
        return item, state, Remoteness(raw)
    except ValueError as exc:
        raise InvalidQueryError(
            f"remoteness must be one of {[tier.value for tier in Remoteness]}"
        ) from exc


class GetPriceLimit:
    """Use case: look up a support item and its deterministic price limit."""

    def __init__(self, repository: SupportItemRepository) -> None:
        self._repository = repository

    def __call__(self, query: PriceLimitQuery) -> PriceLimitResult:
        item_number, state, remoteness = normalise_price_query(query)
        item = self._repository.get(item_number)
        if item is None:
            raise UnknownSupportItemError(
                f"support item {item_number!r} is not in the {FINANCIAL_YEAR} support catalogue"
            )
        limit = (
            price_limit(item, state, remoteness)
            if state is not None
            else item.limits.for_remoteness(remoteness)
        )
        return PriceLimitResult(item=item, state=state, remoteness=remoteness, limit=limit)


# The search endpoint's page size: how many matches one ``/support-items`` call returns.
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 50


@dataclass(frozen=True)
class ServiceSearchResult:
    """One service-phrase search: the phrase as written, the deterministic match list and
    its total (the caller caps what it shows; the total is always stated)."""

    phrase: str
    items: tuple[SupportItem, ...]

    @property
    def total(self) -> int:
        return len(self.items)


class SearchSupportItems:
    """Use case: map a service phrase to catalogue items in code — no model, no key.

    The phrase is capped at the same length the intent parser enforces; the matching,
    ordering and totals come from ``domain.service_search``."""

    def __init__(self, repository: SupportItemRepository) -> None:
        self._repository = repository

    def __call__(self, phrase: str) -> ServiceSearchResult:
        text = " ".join(phrase.strip().split())
        if not text:
            raise InvalidQueryError("service phrase must not be blank")
        if len(text) > MAX_SERVICE_CHARS:
            raise InvalidQueryError(
                f"service phrase must be at most {MAX_SERVICE_CHARS} characters"
            )
        search = search_service_items(text, self._repository.all_items())
        return ServiceSearchResult(search.phrase, search.matches)
