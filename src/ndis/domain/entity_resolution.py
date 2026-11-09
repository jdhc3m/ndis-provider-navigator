"""Pure rules for linking Google Maps places to register providers (entity resolution).

The Maps export is local bootstrap material with no ABN, so a place is linked by website
domain first, then by normalised name, then by a fuzzy name score supplied by an injected
scorer. The register is the only registration evidence: a register hit decides the status,
the Google category only separates "unregistered provider" from "not a provider at all",
and fuzzy hits carry a visibly lower confidence than exact ones.
"""

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ndis.domain.entities import Provider


class MatchStatus(StrEnum):
    """Outcome of resolving one Maps place against the register."""

    REGISTERED = "registered"
    UNREGISTERED = "unregistered"
    REVOKED_OR_BANNED = "revoked_or_banned"
    AMBIGUOUS = "ambiguous"
    NOT_A_PROVIDER = "not_a_provider"


class MatchStrategy(StrEnum):
    """Which rule produced the outcome; ``NONE`` means no rule found a register candidate."""

    DOMAIN = "domain"
    NAME = "name"
    FUZZY = "fuzzy"
    CATEGORY = "category"
    NONE = "none"


@dataclass(frozen=True)
class MapsPlace:
    """The public-safe fields of one row of the private Maps export."""

    place_id: str
    title: str
    category: str | None
    website: str | None
    website_domain: str | None
    suburb: str | None
    postcode: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class EntityMatch:
    """The decision for one place: at most one ABN, a status, the rule used and a confidence."""

    place_id: str
    abn: str | None
    match_status: MatchStatus
    strategy: MatchStrategy
    confidence: float
    matched_name: str | None


@dataclass(frozen=True)
class ProviderLink:
    """A match together with the place it was made for, as persisted in ``provider_links``."""

    place: MapsPlace
    match: EntityMatch


# Tokens dropped from the end of a name (repeatedly) and ``the`` dropped from the start.
LEGAL_SUFFIXES = frozenset({"pty", "ltd", "limited", "inc", "incorporated", "co", "pl"})
LEADING_ARTICLE = "the"

# Hosts shared by unrelated businesses; a Maps website on one of these is no domain evidence.
GENERIC_DOMAINS = frozenset(
    {"facebook.com", "instagram.com", "linkedin.com", "gmail.com", "google.com"}
)

# Google primary categories that describe something other than a disability, health or care
# service. Unknown and blank categories are *not* in this set and are treated as providers.
NON_PROVIDER_CATEGORIES = frozenset(
    {
        "Accounting firm",
        "Adventure sports center",
        "Business broker",
        "Business management consultant",
        "Business to business service",
        "College",
        "Computer support and services",
        "Consultant",
        "Corporate office",
        "Coworking space",
        "Educational institution",
        "Employment center",
        "Fitness center",
        "Gym",
        "House cleaning service",
        "Insurance company",
        "Internet service provider",
        "Labor union",
        "Legal services",
        "Logistics service",
        "Physical fitness program",
        "Property investment company",
        "Real estate agent",
        "Recruitment Agency",
        "Removalist",
        "Software company",
        "State government office",
        "Training center",
        "Transportation service",
    }
)

# ``rapidfuzz.fuzz.ratio`` (0–100) of the normalised title head against a normalised register
# name. On the real export 92 is the lowest score at which every hit is the same business with
# decoration or spacing differences; the first false positive sits at 91.3.
FUZZY_THRESHOLD = 92.0

CONFIDENCE_DOMAIN = 0.95
CONFIDENCE_NAME = 0.90
TIE_BREAK_PENALTY = 0.10
CONFIDENCE_FUZZY_MIN = 0.50
CONFIDENCE_FUZZY_MAX = 0.70
CONFIDENCE_NOT_A_PROVIDER = 0.90
CONFIDENCE_UNREGISTERED_WITH_WEBSITE = 0.60
CONFIDENCE_UNREGISTERED_NO_WEBSITE = 0.40
CONFIDENCE_AMBIGUOUS = 0.30

_TITLE_SEPARATOR = re.compile(r"\s+[-–—|•]\s+|[:,]\s|\s*\(")

type FuzzyCandidates = Callable[[], Iterable[tuple[Provider, float]]]


def normalise_name(name: str) -> str:
    """Canonical comparison key: lower-case, ``&`` as ``and``, apostrophes removed, other
    punctuation as spaces, leading ``the`` and trailing legal suffixes dropped, spaces collapsed.
    """
    text = name.lower().replace("&", " and ").replace("'", "").replace("\u2019", "")
    tokens = "".join(char if char.isalnum() else " " for char in text).split()
    if tokens and tokens[0] == LEADING_ARTICLE:
        tokens = tokens[1:]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def title_head(title: str) -> str:
    """The business-name part of a Maps title, before ``-``, ``|``, ``:``, ``,`` or ``(``
    decoration such as ``Acme Care - NDIS Provider Brisbane``."""
    head = _TITLE_SEPARATOR.split(title, maxsplit=1)[0].strip()
    return head or title.strip()


def is_provider_category(category: str | None) -> bool:
    """False only for categories explicitly listed as non-provider; blank or unknown is True."""
    if category is None:
        return True
    return category.strip() not in NON_PROVIDER_CATEGORIES


def is_generic_domain(domain: str | None) -> bool:
    return domain is not None and domain in GENERIC_DOMAINS


def select_candidates(
    place: MapsPlace, candidates: Iterable[Provider]
) -> tuple[tuple[Provider, ...], bool]:
    """Reduce candidates sharing one key (a franchise domain, a common name) to the best set.

    Candidates whose head office postcode equals the place's postcode win; failing that,
    Brisbane-area candidates win. Returns the distinct-ABN candidates kept and whether a
    tie-break was needed to get there.
    """
    distinct: dict[str, Provider] = {}
    for candidate in candidates:
        distinct.setdefault(candidate.abn, candidate)
    kept = tuple(distinct.values())
    if len(kept) <= 1:
        return kept, False
    same_postcode = tuple(c for c in kept if c.head_office.postcode == place.postcode)
    if same_postcode:
        return same_postcode, True
    in_brisbane = tuple(c for c in kept if c.brisbane_area)
    if in_brisbane:
        return in_brisbane, True
    return kept, True


def fuzzy_confidence(score: float) -> float:
    """Map a score in ``[FUZZY_THRESHOLD, 100]`` onto ``[CONFIDENCE_FUZZY_MIN, _MAX]``."""
    span = 100.0 - FUZZY_THRESHOLD
    fraction = min(max((score - FUZZY_THRESHOLD) / span, 0.0), 1.0)
    return round(CONFIDENCE_FUZZY_MIN + fraction * (CONFIDENCE_FUZZY_MAX - CONFIDENCE_FUZZY_MIN), 3)


def _match_from_candidates(
    place: MapsPlace,
    strategy: MatchStrategy,
    candidates: Iterable[Provider],
    confidence: float,
) -> EntityMatch | None:
    kept, tie_broken = select_candidates(place, candidates)
    if not kept:
        return None
    if len(kept) > 1:
        return EntityMatch(
            place.place_id, None, MatchStatus.AMBIGUOUS, strategy, CONFIDENCE_AMBIGUOUS, None
        )
    provider = kept[0]
    status = (
        MatchStatus.REGISTERED
        if provider.registration_status == "Approved"
        else MatchStatus.REVOKED_OR_BANNED
    )
    if tie_broken:
        confidence = round(confidence - TIE_BREAK_PENALTY, 3)
    return EntityMatch(
        place.place_id, provider.abn, status, strategy, confidence, provider.business_name
    )


def best_fuzzy_candidates(
    scored: Iterable[tuple[Provider, float]], threshold: float = FUZZY_THRESHOLD
) -> tuple[tuple[Provider, ...], float]:
    """The candidates sharing the highest score at or above ``threshold`` and that score."""
    best: list[Provider] = []
    best_score = threshold
    for provider, score in scored:
        if score < threshold:
            continue
        if score > best_score or not best:
            best, best_score = [provider], score
        elif score == best_score:
            best.append(provider)
    return tuple(best), best_score


def resolve_place(
    place: MapsPlace,
    domain_candidates: Sequence[Provider],
    name_candidates: Sequence[Provider],
    fuzzy_candidates: FuzzyCandidates,
    threshold: float = FUZZY_THRESHOLD,
) -> EntityMatch:
    """Decide the match for one place.

    Order: website domain (unless generic), normalised name, the category rule, then the
    fuzzy scorer over the candidates ``fuzzy_candidates`` yields (the Brisbane-area subset;
    called only when needed because it is the expensive step). A register hit beats the
    category rule because the register is authoritative about who is a provider; the fuzzy
    rule runs after the category rule because fuzzy hits are not authoritative.
    """
    if not is_generic_domain(place.website_domain):
        match = _match_from_candidates(
            place, MatchStrategy.DOMAIN, domain_candidates, CONFIDENCE_DOMAIN
        )
        if match is not None:
            return match
    match = _match_from_candidates(place, MatchStrategy.NAME, name_candidates, CONFIDENCE_NAME)
    if match is not None:
        return match
    if not is_provider_category(place.category):
        return EntityMatch(
            place.place_id,
            None,
            MatchStatus.NOT_A_PROVIDER,
            MatchStrategy.CATEGORY,
            CONFIDENCE_NOT_A_PROVIDER,
            None,
        )
    best, score = best_fuzzy_candidates(fuzzy_candidates(), threshold)
    match = _match_from_candidates(place, MatchStrategy.FUZZY, best, fuzzy_confidence(score))
    if match is not None:
        return match
    confidence = (
        CONFIDENCE_UNREGISTERED_WITH_WEBSITE
        if place.website_domain
        else CONFIDENCE_UNREGISTERED_NO_WEBSITE
    )
    return EntityMatch(
        place.place_id, None, MatchStatus.UNREGISTERED, MatchStrategy.NONE, confidence, None
    )
