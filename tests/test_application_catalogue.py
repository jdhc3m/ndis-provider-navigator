"""Get-price-limit and service-search use cases and query normalisation against a fake
repository."""

from collections.abc import Iterable
from decimal import Decimal

import pytest

from ndis.application.catalogue import (
    GetPriceLimit,
    PriceLimitQuery,
    SearchSupportItems,
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

    def all_items(self) -> tuple[SupportItem, ...]:
        return tuple(self.items.values())


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


def test_an_item_without_a_state_answers_the_national_limit(use_case: GetPriceLimit) -> None:
    """The /ask path when the question states no state: the national tier applies, the
    result says so with ``state=None``, and the endpoint contract is untouched."""
    result = use_case(PriceLimitQuery(item="01_002_0107_1_1", state=None))
    assert result.state is None
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


def test_normalise_price_query_keeps_a_missing_state() -> None:
    assert normalise_price_query(PriceLimitQuery(item="x", state=None)) == (
        "x",
        None,
        Remoteness.NATIONAL,
    )


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


@pytest.fixture
def search() -> SearchSupportItems:
    repository = FakeRepository()
    repository.replace_all(SEED_SUPPORT_ITEMS)
    return SearchSupportItems(repository)


class TestSearchSupportItems:
    def test_a_phrase_matches_by_name_tokens(self, search: SearchSupportItems) -> None:
        result = search("self-care activities")
        assert result.total == 1
        assert result.items[0].item_number == "01_002_0107_1_1"

    def test_the_phrase_is_trimmed_but_kept_verbatim(self, search: SearchSupportItems) -> None:
        assert search("  self-care activities  ").phrase == "self-care activities"

    def test_no_match_is_an_empty_result(self, search: SearchSupportItems) -> None:
        result = search("underwater basket weaving")
        assert result.total == 0 and result.items == ()

    @pytest.mark.parametrize("phrase", ["", "   "])
    def test_a_blank_phrase_is_rejected(self, search: SearchSupportItems, phrase: str) -> None:
        with pytest.raises(InvalidQueryError, match="service phrase must not be blank"):
            search(phrase)

    def test_an_overlong_phrase_is_rejected(self, search: SearchSupportItems) -> None:
        with pytest.raises(InvalidQueryError, match="at most 80 characters"):
            search("x" * 81)
