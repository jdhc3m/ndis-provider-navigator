"""Precision over hand-checked verdicts and the deterministic stratified sample."""

from collections import Counter

import pytest

from ndis.domain.entity_resolution import (
    EntityMatch,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
)
from ndis.domain.evaluation import (
    InvalidVerdictError,
    PrecisionResult,
    precision_from_verdicts,
    stratified_sample,
)
from tests.conftest import make_place


def test_precision_with_no_labels_is_distinguishable_from_zero() -> None:
    result = precision_from_verdicts([None] * 50)
    assert result == PrecisionResult(total=50, labelled=0, correct=0, unsure=0, precision=None)
    assert result.summary() == "0 of 50 verdicts filled; no precision yet"
    assert precision_from_verdicts([]).precision is None


def test_precision_counts_only_correct_and_incorrect() -> None:
    # 4 correct, 2 incorrect, 1 unsure, 3 blank -> precision 4/6.
    verdicts = ["correct", "incorrect", None, "correct", "unsure", "correct", None, "incorrect",
                "correct", None]  # fmt: skip
    result = precision_from_verdicts(verdicts)
    assert (result.total, result.labelled, result.correct, result.unsure) == (10, 6, 4, 1)
    assert result.precision == pytest.approx(4 / 6)
    assert result.summary() == "6 of 10 verdicts filled (1 unsure); precision 0.667 (4/6)"
    assert precision_from_verdicts(["incorrect", "incorrect"]).precision == 0.0


def test_unknown_verdict_is_rejected() -> None:
    with pytest.raises(InvalidVerdictError, match="verdict must be one of"):
        precision_from_verdicts(["correct", "yes"])


def link(
    index: int, status: MatchStatus, strategy: MatchStrategy = MatchStrategy.NONE
) -> ProviderLink:
    place = make_place(f"place-{index:03d}", f"Title {index}")
    match = EntityMatch(place.place_id, None, status, strategy, 0.5, None)
    return ProviderLink(place, match)


LINKS = (
    [link(i, MatchStatus.REGISTERED, MatchStrategy.DOMAIN) for i in range(60)]
    + [link(100 + i, MatchStatus.UNREGISTERED) for i in range(30)]
    + [link(200 + i, MatchStatus.NOT_A_PROVIDER, MatchStrategy.CATEGORY) for i in range(9)]
    + [link(300, MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN)]
    + [link(400, MatchStatus.REGISTERED, MatchStrategy.FUZZY)]
)


def test_stratified_sample_is_deterministic_and_covers_every_stratum() -> None:
    first = stratified_sample(LINKS, 20, seed=7)
    assert first == stratified_sample(list(reversed(LINKS)), 20, seed=7)
    assert first != stratified_sample(LINKS, 20, seed=8)
    assert len(first) == 20
    assert len({row.place.place_id for row in first}) == 20
    counts = Counter((row.match.match_status, row.match.strategy) for row in first)
    # One row per (status, strategy) first (5), the other 15 in proportion to 60:30:9:1:1 of
    # 101 -> 8.9:4.5:1.3:0.1:0.1 -> 8:4:1:0:0, the last two by largest remainder.
    assert counts == {
        (MatchStatus.REGISTERED, MatchStrategy.DOMAIN): 10,
        (MatchStatus.UNREGISTERED, MatchStrategy.NONE): 6,
        (MatchStatus.NOT_A_PROVIDER, MatchStrategy.CATEGORY): 2,
        (MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN): 1,
        (MatchStatus.REGISTERED, MatchStrategy.FUZZY): 1,
    }
    # The single fuzzy link is always drawn, whatever the seed.
    assert all(LINKS[-1] in stratified_sample(LINKS, 20, seed=seed) for seed in range(5))


def test_stratified_sample_edge_sizes() -> None:
    assert stratified_sample([], 10, seed=1) == ()
    assert stratified_sample(LINKS, 0, seed=1) == ()
    assert len(stratified_sample(LINKS[:3], 10, seed=1)) == 3
    strata = {(r.match.match_status, r.match.strategy) for r in stratified_sample(LINKS, 5, seed=1)}
    assert strata == {
        (MatchStatus.REGISTERED, MatchStrategy.DOMAIN),
        (MatchStatus.UNREGISTERED, MatchStrategy.NONE),
        (MatchStatus.NOT_A_PROVIDER, MatchStrategy.CATEGORY),
        (MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN),
        (MatchStatus.REGISTERED, MatchStrategy.FUZZY),
    }
