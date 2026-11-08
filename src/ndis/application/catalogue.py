"""Support item repository port, price-limit query types and the get-price-limit use case."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ndis.application.providers import InvalidQueryError
from ndis.domain.catalogue import FINANCIAL_YEAR, price_limit
from ndis.domain.entities import Remoteness, SupportItem
from ndis.domain.register import STATES


@dataclass(frozen=True)
class PriceLimitQuery:
    """Which support item, state and remoteness tier to price.

    ``remoteness`` is one of the ``Remoteness`` values (matched case-insensitively) and
    defaults to the national, non-remote tier when blank.
    """

    item: str
    state: str
    remoteness: str | None = None


@dataclass(frozen=True)
class PriceLimitResult:
    """The item, the normalised query and the limit that applies (``None`` when unpublished)."""

    item: SupportItem
    state: str
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


class UnknownSupportItemError(LookupError):
    """The requested support item number is not in the loaded catalogue."""


def normalise_price_query(query: PriceLimitQuery) -> tuple[str, str, Remoteness]:
    """Validate and canonicalise the query: trimmed item, upper-case state, remoteness enum."""
    item = query.item.strip()
    if not item:
        raise InvalidQueryError("item must not be blank")
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
        return PriceLimitResult(
            item=item,
            state=state,
            remoteness=remoteness,
            limit=price_limit(item, state, remoteness),
        )
