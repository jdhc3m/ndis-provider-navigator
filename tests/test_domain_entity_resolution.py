"""Pure entity-resolution rules: name normalisation, category rule, decision and confidence."""

from dataclasses import replace

import pytest

from ndis.domain.entities import Provider
from ndis.domain.entity_resolution import (
    CONFIDENCE_AMBIGUOUS,
    CONFIDENCE_DOMAIN,
    CONFIDENCE_FUZZY_MAX,
    CONFIDENCE_FUZZY_MIN,
    CONFIDENCE_NAME,
    CONFIDENCE_NOT_A_PROVIDER,
    CONFIDENCE_UNREGISTERED_NO_WEBSITE,
    CONFIDENCE_UNREGISTERED_WITH_WEBSITE,
    FUZZY_THRESHOLD,
    TIE_BREAK_PENALTY,
    EntityMatch,
    MatchStatus,
    MatchStrategy,
    best_fuzzy_candidates,
    fuzzy_confidence,
    is_generic_domain,
    is_provider_category,
    normalise_name,
    resolve_place,
    select_candidates,
    title_head,
)
from tests.conftest import SEED_PROVIDERS, make_place

ALPHA, BETA, GAMMA, DELTA_REVOKED, EPSILON = SEED_PROVIDERS


def no_fuzzy() -> list[tuple[Provider, float]]:
    raise AssertionError("fuzzy scoring must not run when an earlier rule decides")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Smith, Jones \"Care\" Pty Ltd", "smith jones care"),
        ("K & M HEALTHCARE PTY. LTD.", "k and m healthcare"),
        ("Amma's Care Support Services", "ammas care support services"),
        ("Jac\u2019s Place Supported Living", "jacs place supported living"),
        ("The Adventure Team", "adventure team"),
        ("Speaking Up for You Inc.", "speaking up for you"),
        ("HUNTER CARERS LTD", "hunter carers"),
        ("CATHOLIC HEALTHCARE LIMITED", "catholic healthcare"),
        ("WE CARE DISABILITY SERVICES (QUIRINDI)Pty Ltd", "we care disability services quirindi"),
        ("Vitium Cura - NDIS Support Services", "vitium cura ndis support services"),
        ("  Double   Space\tName ", "double space name"),
        ("The Pty Ltd", ""),
        ("Café Ability Pty Ltd", "café ability"),
    ],
)  # fmt: skip
def test_normalise_name(name: str, expected: str) -> None:
    assert normalise_name(name) == expected


def test_normalise_name_only_strips_trailing_suffixes_and_leading_the() -> None:
    assert normalise_name("Inc Lifestyle The Hub Ltd") == "inc lifestyle the hub"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Vitium Cura - NDIS Disability Support Services", "Vitium Cura"),
        ("Wayfare Support \u2013 NDIS Provider Brisbane", "Wayfare Support"),
        ("Easy Healthcare | Brisbane", "Easy Healthcare"),
        ("Athletix Gym Brisbane: Strength & Conditioning", "Athletix Gym Brisbane"),
        ("Rainbow Inclusions (RI) - Disability Support", "Rainbow Inclusions"),
        ("Disability Services Queensland, Nundah", "Disability Services Queensland"),
        ("I-Help Disability Services", "I-Help Disability Services"),
        ("- odd -", "- odd -"),
    ],
)
def test_title_head(title: str, expected: str) -> None:
    assert title_head(title) == expected


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("Disability services and support organization", True),
        ("Physiotherapist", True),
        ("Recruitment Agency", False),
        ("Software company", False),
        (" Corporate office ", False),
        ("Retirement community", True),
        ("Some category Google invents next year", True),
        ("", True),
        (None, True),
    ],
)
def test_is_provider_category(category: str | None, expected: bool) -> None:
    assert is_provider_category(category) is expected


def test_generic_domains_are_not_evidence() -> None:
    assert is_generic_domain("facebook.com")
    assert not is_generic_domain("huntercarers.org.au")
    assert not is_generic_domain(None)


def test_select_candidates_dedupes_and_tie_breaks_by_postcode_then_brisbane() -> None:
    place = make_place("p", "Franchise", postcode="4000")
    assert select_candidates(place, [ALPHA, ALPHA]) == ((ALPHA,), False)
    # BETA is at 4000, so it wins over ALPHA (4101) on postcode.
    assert select_candidates(place, [ALPHA, BETA]) == ((BETA,), True)
    # Neither matches 4000; GAMMA is outside Brisbane so ALPHA wins on the area flag.
    far = make_place("p", "Franchise", postcode="4999")
    assert select_candidates(far, [GAMMA, ALPHA]) == ((ALPHA,), True)
    # Two non-Brisbane candidates and no postcode hit: nothing to choose between.
    interstate = replace(ALPHA, abn="10000000009", brisbane_area=False)
    assert select_candidates(far, [GAMMA, interstate]) == ((GAMMA, interstate), True)
    assert select_candidates(far, []) == ((), False)


def test_domain_hit_is_registered_with_top_confidence() -> None:
    place = make_place("p", "Whatever Title", website="https://www.10000000001.example.com.au/x")
    match = resolve_place(place, [ALPHA], [], no_fuzzy)
    assert match == EntityMatch(
        "p",
        ALPHA.abn,
        MatchStatus.REGISTERED,
        MatchStrategy.DOMAIN,
        CONFIDENCE_DOMAIN,
        ALPHA.business_name,
    )


def test_generic_domain_falls_through_to_name() -> None:
    place = make_place("p", "Beta Supports", website="https://facebook.com/beta")
    match = resolve_place(place, [ALPHA, BETA], [BETA], no_fuzzy)
    assert (match.strategy, match.abn, match.confidence) == (
        MatchStrategy.NAME,
        BETA.abn,
        CONFIDENCE_NAME,
    )


def test_revoked_register_match_is_revoked_or_banned() -> None:
    match = resolve_place(make_place("p", "Delta Revoked"), [], [DELTA_REVOKED], no_fuzzy)
    assert match.match_status is MatchStatus.REVOKED_OR_BANNED
    assert match.abn == DELTA_REVOKED.abn


def test_franchise_domain_resolved_by_postcode_costs_confidence() -> None:
    place = make_place(
        "p", "Franchise Brisbane", website="https://franchise.example.com", postcode="4000"
    )
    match = resolve_place(place, [ALPHA, BETA], [], no_fuzzy)
    assert (match.abn, match.strategy) == (BETA.abn, MatchStrategy.DOMAIN)
    assert match.confidence == pytest.approx(CONFIDENCE_DOMAIN - TIE_BREAK_PENALTY)


def test_equally_good_candidates_are_ambiguous() -> None:
    place = make_place("p", "Franchise", website="https://franchise.example.com", postcode="4999")
    match = resolve_place(place, [ALPHA, BETA], [], no_fuzzy)
    assert match == EntityMatch(
        "p", None, MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN, CONFIDENCE_AMBIGUOUS, None
    )


def test_non_provider_category_without_register_hit() -> None:
    place = make_place(
        "p", "Hire Co", category="Recruitment Agency", website="https://hireco.example.com"
    )
    match = resolve_place(place, [], [], no_fuzzy)
    assert match == EntityMatch(
        "p",
        None,
        MatchStatus.NOT_A_PROVIDER,
        MatchStrategy.CATEGORY,
        CONFIDENCE_NOT_A_PROVIDER,
        None,
    )


def test_register_hit_beats_the_category_rule() -> None:
    place = make_place(
        "p", "Hire Co", category="Recruitment Agency", website="https://hireco.example.com"
    )
    match = resolve_place(place, [ALPHA], [], no_fuzzy)
    assert match.match_status is MatchStatus.REGISTERED


@pytest.mark.parametrize(
    ("score", "status", "confidence"),
    [
        (FUZZY_THRESHOLD, MatchStatus.REGISTERED, CONFIDENCE_FUZZY_MIN),
        (100.0, MatchStatus.REGISTERED, CONFIDENCE_FUZZY_MAX),
        (FUZZY_THRESHOLD - 0.1, MatchStatus.UNREGISTERED, CONFIDENCE_UNREGISTERED_WITH_WEBSITE),
    ],
)
def test_fuzzy_threshold_boundary(score: float, status: MatchStatus, confidence: float) -> None:
    place = make_place("p", "Alpha Therapi", website="https://unknown.example.com")
    match = resolve_place(place, [], [], lambda: [(ALPHA, score), (BETA, 40.0)])
    assert match.match_status is status
    assert match.confidence == pytest.approx(confidence)
    if status is MatchStatus.REGISTERED:
        assert (match.strategy, match.abn) == (MatchStrategy.FUZZY, ALPHA.abn)
        assert match.confidence < CONFIDENCE_NAME
    else:
        assert (match.strategy, match.abn) == (MatchStrategy.NONE, None)


def test_fuzzy_confidence_is_monotonic_and_bounded() -> None:
    mid = (FUZZY_THRESHOLD + 100) / 2
    assert CONFIDENCE_FUZZY_MIN < fuzzy_confidence(mid) < CONFIDENCE_FUZZY_MAX
    assert fuzzy_confidence(50.0) == CONFIDENCE_FUZZY_MIN
    assert fuzzy_confidence(150.0) == CONFIDENCE_FUZZY_MAX


def test_best_fuzzy_candidates_keeps_ties_at_the_top_score() -> None:
    scored = [(ALPHA, 95.0), (BETA, 97.5), (GAMMA, 97.5), (EPSILON, 91.0)]
    assert best_fuzzy_candidates(scored) == ((BETA, GAMMA), 97.5)
    assert best_fuzzy_candidates([(ALPHA, 10.0)]) == ((), FUZZY_THRESHOLD)
    assert best_fuzzy_candidates(scored, threshold=99.0) == ((), 99.0)


def test_tied_fuzzy_candidates_outside_brisbane_are_ambiguous() -> None:
    interstate = replace(ALPHA, abn="10000000009", brisbane_area=False)
    place = make_place("p", "Tie", website=None, postcode="4999")
    match = resolve_place(place, [], [], lambda: [(GAMMA, 96.0), (interstate, 96.0)])
    assert (match.match_status, match.strategy) == (MatchStatus.AMBIGUOUS, MatchStrategy.FUZZY)


def test_unregistered_without_website_has_lower_confidence() -> None:
    match = resolve_place(make_place("p", "Nobody"), [], [], lambda: [])
    assert match.match_status is MatchStatus.UNREGISTERED
    assert match.confidence == CONFIDENCE_UNREGISTERED_NO_WEBSITE
