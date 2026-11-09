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
    GOVERNMENT = "government"
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
class Candidate:
    """A register provider an ``ambiguous`` place could be, listed so a person can decide."""

    abn: str
    name: str


@dataclass(frozen=True)
class EntityMatch:
    """The decision for one place: at most one ABN, a status, the rule used and a confidence.

    ``candidates`` is filled only for ``ambiguous`` outcomes: the register providers the
    rule found but could not tell apart (or, for a lone name hit far from the place, the one
    provider nothing ties to it)."""

    place_id: str
    abn: str | None
    match_status: MatchStatus
    strategy: MatchStrategy
    confidence: float
    matched_name: str | None
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class ProviderLink:
    """A match together with the place it was made for, as persisted in ``provider_links``."""

    place: MapsPlace
    match: EntityMatch


# Tokens dropped from the end of a name (repeatedly) and ``the`` dropped from the start.
LEGAL_SUFFIXES = frozenset({"pty", "ltd", "limited", "inc", "incorporated", "co", "pl"})
LEADING_ARTICLE = "the"

# Hosts (and their subdomains) shared by unrelated businesses: social profiles and free site
# builders. A Maps website on one of these is no domain evidence.
GENERIC_DOMAINS = frozenset(
    {
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "gmail.com",
        "google.com",
        "sites.google.com",
        "wixsite.com",
        "linktr.ee",
        "business.site",
        "wordpress.com",
        "squarespace.com",
        "godaddysites.com",
    }
)

# Bare public suffixes that a mangled website field can reduce to (the register lists
# ``com.au`` as the domain of two providers); never evidence of anything.
PUBLIC_SUFFIXES = frozenset(
    {"au", "com", "net", "org", "co", "io", "com.au", "net.au", "org.au", "edu.au", "gov.au",
     "id.au", "asn.au", "nz", "co.nz"}
)  # fmt: skip

# Government agencies (the NDIA, state departments, the health ombudsman) list on Maps but
# are not providers unless the register says so.
GOVERNMENT_SUFFIX = "gov.au"

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
    """True for hosts that identify nobody: a shared platform (``facebook.com``,
    ``acme.wixsite.com``) or a bare public suffix such as ``com.au``."""
    if domain is None:
        return False
    if domain in PUBLIC_SUFFIXES:
        return True
    return any(domain == shared or domain.endswith("." + shared) for shared in GENERIC_DOMAINS)


def is_government_domain(domain: str | None) -> bool:
    """True for ``gov.au`` hosts and their subdomains."""
    return domain is not None and (
        domain == GOVERNMENT_SUFFIX or domain.endswith("." + GOVERNMENT_SUFFIX)
    )


def name_keys(provider: Provider) -> frozenset[str]:
    """The non-empty normalised business and legal names of a provider."""
    return frozenset(
        key
        for key in (normalise_name(provider.business_name), normalise_name(provider.legal_name))
        if key
    )


def title_keys(title: str) -> frozenset[str]:
    """The non-empty normalised Maps title and title head; what the name rule looks up."""
    return frozenset(
        key for key in (normalise_name(title), normalise_name(title_head(title))) if key
    )


def _at_place_postcode(place: MapsPlace, provider: Provider) -> bool:
    return place.postcode is not None and provider.head_office.postcode == place.postcode


def select_candidates(
    place: MapsPlace, candidates: Iterable[Provider]
) -> tuple[tuple[Provider, ...], bool]:
    """Reduce candidates sharing one key (a franchise domain, a common name) to the best set.

    Only two things may break a tie: a candidate whose normalised name equals the normalised
    title or title head (a franchisee named like the listing), then a candidate whose head
    office postcode equals the place's postcode. Merely being in the Brisbane area does not,
    because a franchise with one Brisbane franchisee would otherwise absorb every other
    listing on the shared domain. Returns the distinct-ABN candidates kept and whether a
    tie-break was needed to get there.
    """
    distinct: dict[str, Provider] = {}
    for candidate in candidates:
        distinct.setdefault(candidate.abn, candidate)
    kept = tuple(distinct.values())
    if len(kept) <= 1:
        return kept, False
    keys = title_keys(place.title)
    same_name = tuple(c for c in kept if name_keys(c) & keys)
    if same_name:
        kept = same_name
    if len(kept) > 1:
        same_postcode = tuple(c for c in kept if _at_place_postcode(place, c))
        if same_postcode:
            kept = same_postcode
    return kept, True


def fuzzy_confidence(score: float) -> float:
    """Map a score in ``[FUZZY_THRESHOLD, 100]`` onto ``[CONFIDENCE_FUZZY_MIN, _MAX]``."""
    span = 100.0 - FUZZY_THRESHOLD
    fraction = min(max((score - FUZZY_THRESHOLD) / span, 0.0), 1.0)
    return round(CONFIDENCE_FUZZY_MIN + fraction * (CONFIDENCE_FUZZY_MAX - CONFIDENCE_FUZZY_MIN), 3)


def _ambiguous(
    place: MapsPlace, strategy: MatchStrategy, candidates: Sequence[Provider]
) -> EntityMatch:
    return EntityMatch(
        place.place_id,
        None,
        MatchStatus.AMBIGUOUS,
        strategy,
        CONFIDENCE_AMBIGUOUS,
        None,
        tuple(Candidate(c.abn, c.business_name) for c in candidates),
    )


def _not_a_provider(place: MapsPlace, strategy: MatchStrategy) -> EntityMatch:
    return EntityMatch(
        place.place_id, None, MatchStatus.NOT_A_PROVIDER, strategy, CONFIDENCE_NOT_A_PROVIDER, None
    )


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
        return _ambiguous(place, strategy, kept)
    provider = kept[0]
    if strategy is not MatchStrategy.DOMAIN and not (
        provider.brisbane_area or _at_place_postcode(place, provider)
    ):
        # A name alone shared with one provider nowhere near the place is no evidence that
        # it is this business; a domain hit to an interstate head office is.
        return _ambiguous(place, strategy, kept)
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

    Order: website domain (unless generic), normalised name, the government-domain and
    category rules, then the fuzzy scorer over the candidates ``fuzzy_candidates`` yields
    (the Brisbane-area subset; called only when needed because it is the expensive step). A
    register hit beats the non-provider rules because the register is authoritative about
    who is a provider (councils and health departments are registered); the fuzzy rule runs
    after them because fuzzy hits are not authoritative.
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
    if is_government_domain(place.website_domain):
        return _not_a_provider(place, MatchStrategy.GOVERNMENT)
    if not is_provider_category(place.category):
        return _not_a_provider(place, MatchStrategy.CATEGORY)
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
