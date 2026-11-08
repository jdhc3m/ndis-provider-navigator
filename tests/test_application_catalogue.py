"""Get-price-limit use case and query normalisation against a fake repository."""

from collections.abc import Iterable
from decimal import Decimal

import pytest

from ndis.application.catalogue import (
    GetPriceLimit,
    PriceLimitQuery,
    UnknownSupportItemError,
    normalise_price_query,
)
from ndis.application.providers import InvalidQueryError
from ndis.domain.entities import Remoteness, SupportItem
from tests.conftest import SEED_SUPPORT_ITEMS


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[str, SupportItem] = {}
        self.lookups: list[str] = []

    def replace_all(self, items: Iterable[SupportItem]) -> None:
        self.items = {item.item_number: item for item in items}

    def get(self, item_number: str) -> SupportItem | None:
        self.lookups.append(item_number)
        return self.items.get(item_number)


@pytest.fixture
def use_case() -> GetPriceLimit:
    repository = FakeRepository()
    repository.replace_all(SEED_SUPPORT_ITEMS)
    return GetPriceLimit(repository)


def test_use_case_normalises_and_returns_the_limit(use_case: GetPriceLimit) -> None:
    result = use_case(PriceLimitQuery(item=" 01_002_0107_1_1 ", state="qld", remoteness=" Remote "))
    assert result.item is SEED_SUPPORT_ITEMS[0]
    assert (result.state, result.remoteness) == ("QLD", Remoteness.REMOTE)
    assert result.limit == Decimal("115.60")
    assert use_case._repository.lookups == ["01_002_0107_1_1"]  # type: ignore[attr-defined]


def test_remoteness_defaults_to_national(use_case: GetPriceLimit) -> None:
    for remoteness in (None, "", "  "):
        query = PriceLimitQuery(item="01_002_0107_1_1", state="NSW", remoteness=remoteness)
        result = use_case(query)
        assert result.remoteness is Remoteness.NATIONAL
        assert result.limit == Decimal("82.57")


def test_quotable_item_has_no_limit(use_case: GetPriceLimit) -> None:
    result = use_case(PriceLimitQuery(item="01_003_0107_1_1", state="QLD"))
    assert result.item.quotable is True
    assert result.limit is None


def test_unknown_item_raises(use_case: GetPriceLimit) -> None:
    with pytest.raises(UnknownSupportItemError, match="'99_999_0000_0_0' is not in the 2026-27"):
        use_case(PriceLimitQuery(item="99_999_0000_0_0", state="QLD"))


def test_normalise_price_query() -> None:
    query = PriceLimitQuery(item="x", state="wa", remoteness="VERY_REMOTE")
    assert normalise_price_query(query) == ("x", "WA", Remoteness.VERY_REMOTE)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        (PriceLimitQuery(item="", state="QLD"), "item must not be blank"),
        (PriceLimitQuery(item="01_002_0107_1_1", state="NZ"), "state must be one of"),
        (PriceLimitQuery(item="01_002_0107_1_1", state=""), "state must be one of"),
        (PriceLimitQuery(item="01_002_0107_1_1", state="QLD", remoteness="rural"),
         "remoteness must be one of"),
    ],
)  # fmt: skip
def test_invalid_queries_are_rejected_before_lookup(query: PriceLimitQuery, message: str) -> None:
    repository = FakeRepository()
    with pytest.raises(InvalidQueryError, match=message):
        GetPriceLimit(repository)(query)
    assert repository.lookups == []
