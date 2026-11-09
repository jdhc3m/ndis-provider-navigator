"""Pure rules for extracting service claims from page text: what a claim is, when the page
text actually supports it, how model output is parsed, how pages are chosen and budgeted.

Every rule here is deterministic and side-effect free. The model's JSON is treated as
untrusted input: nothing from it is trusted unless it passes these checks, and the parser
never raises on model output — it returns what survived plus machine-readable rejection
reasons. The page text itself is also never echoed back into rejection reasons, so
private page content cannot leak into logs or databases.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from ndis.domain.taxonomy import SERVICES, Service, service_of

# A quote below this many characters (after whitespace collapsing) can match trivial text
# such as "and" or a heading fragment, so it is not evidence. One above the maximum is not
# a quote but a page dump.
MIN_EVIDENCE_CHARS = 25
MAX_EVIDENCE_CHARS = 400

# Model-output hygiene limits: a page that claims dozens of services or speaks twenty
# languages is hallucinating lists, and the claim cap bounds how far one bad page can go.
MAX_CLAIMS_PER_PAGE = 40
MAX_LANGUAGES = 12
MAX_LANGUAGE_CHARS = 40


class AgeGroup(StrEnum):
    """Coarse audience bands a page can claim to serve."""

    EARLY_CHILDHOOD = "early_childhood"
    SCHOOL_AGE = "school_age"
    ADULTS = "adults"
    OLDER_ADULTS = "older_adults"


AGE_GROUPS: tuple[AgeGroup, ...] = tuple(AgeGroup)


@dataclass(frozen=True)
class ServiceClaim:
    """One service the model says a page evidences, with the verbatim quote and the model's
    own confidence. Never trusted until ``quote_supported`` has accepted the quote."""

    service: Service
    evidence_quote: str
    confidence: float


@dataclass(frozen=True)
class PageAttributes:
    """The provider-level facts one page states: who it serves and how it delivers."""

    age_groups: tuple[AgeGroup, ...] = ()
    telehealth: bool | None = None
    home_visits: bool | None = None
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedPage:
    """What survived parsing one page's model output. ``rejected`` holds short machine
    reasons (never page text) for everything dropped, so a run can report its hygiene."""

    claims: tuple[ServiceClaim, ...] = ()
    attributes: PageAttributes = PageAttributes()
    rejected: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredPage:
    """One stored page of a provider: metadata plus the text loaded from the private store."""

    provider_key: str
    url: str
    title: str | None
    text: str


@dataclass(frozen=True)
class PageClaim:
    """An accepted claim tied to the page that supplied its evidence."""

    url: str
    claim: ServiceClaim


@dataclass(frozen=True)
class ProviderExtraction:
    """Everything one provider's extraction produced: accepted claims per page, the merged
    attributes, and the token usage the run paid for. Cached as the unit of resumability."""

    provider_key: str
    claims: tuple[PageClaim, ...]
    attributes: PageAttributes
    extracted_at: datetime
    input_tokens: int
    output_tokens: int

    def services(self) -> frozenset[Service]:
        """The distinct claimed services, the set the evaluation scores."""
        return frozenset(page_claim.claim.service for page_claim in self.claims)


def collapse_whitespace(text: str) -> str:
    """Every run of whitespace (spaces, tabs, newlines) becomes one space, ends trimmed."""
    return " ".join(text.split())


def quote_supported(text: str, quote: str) -> bool:
    """True only when ``quote`` is a genuine piece of ``text``.

    Both sides are whitespace-collapsed, so a quote that spans a line break of the cleaned
    page text still matches, but every other character must match exactly: an inserted
    zero-width space, a swapped apostrophe or a reworded sentence all fail. A quote shorter
    than ``MIN_EVIDENCE_CHARS`` could name a bare heading or the word "and", longer than
    ``MAX_EVIDENCE_CHARS`` is a copied page rather than a quotation, and a quote with no
    alphanumeric character at all is noise; none of those are evidence.
    """
    collapsed_quote = collapse_whitespace(quote)
    if not MIN_EVIDENCE_CHARS <= len(collapsed_quote) <= MAX_EVIDENCE_CHARS:
        return False
    if not any(character.isalnum() for character in collapsed_quote):
        return False
    return collapse_whitespace(text).find(collapsed_quote) >= 0


def parse_extraction(payload: str) -> ParsedPage:
    """Parse one page's model output into claims and attributes.

    ``payload`` is the raw text the model returned, expected to be one JSON object. The
    parser is total: any structural surprise (non-JSON text, wrong types, unknown service
    keys, out-of-range confidence, oversized or unsupported fields) becomes a rejection
    reason, never an exception, so a bad model answer can only ever lose data it brought.
    """
    rejected: list[str] = []
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return ParsedPage(rejected=("not_json",))
    if not isinstance(document, dict):
        return ParsedPage(rejected=("not_object",))

    claims = _parse_claims(document.get("claims"), rejected)
    attributes = PageAttributes(
        age_groups=_parse_age_groups(document.get("age_groups"), rejected),
        telehealth=_parse_flag(document.get("telehealth"), "telehealth", rejected),
        home_visits=_parse_flag(document.get("home_visits"), "home_visits", rejected),
        languages=_parse_languages(document.get("languages"), rejected),
    )
    return ParsedPage(claims=tuple(claims), attributes=attributes, rejected=tuple(rejected))


def _parse_claims(value: object, rejected: list[str]) -> list[ServiceClaim]:
    if value is None:
        rejected.append("claims_missing")
        return []
    if not isinstance(value, list):
        rejected.append("claims_not_list")
        return []
    claims: list[ServiceClaim] = []
    for item in value:
        if len(claims) >= MAX_CLAIMS_PER_PAGE:
            rejected.append("claims_too_many")
            break
        if not isinstance(item, dict):
            rejected.append("claim_not_object")
            continue
        service_key = item.get("service")
        quote = item.get("evidence_quote")
        confidence = item.get("confidence")
        if not isinstance(service_key, str) or service_of(service_key) is None:
            rejected.append("claim_service_unknown")
            continue
        if not isinstance(quote, str):
            rejected.append("claim_quote_not_string")
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            rejected.append("claim_confidence_not_number")
            continue
        confidence_value = float(confidence)
        if not 0.0 <= confidence_value <= 1.0:
            rejected.append("claim_confidence_out_of_range")
            continue
        claims.append(ServiceClaim(service_of(service_key), quote, confidence_value))  # type: ignore[arg-type]
    return claims


def _parse_age_groups(value: object, rejected: list[str]) -> tuple[AgeGroup, ...]:
    if value is None:
        return ()  # the prompt allows leaving this out when the page says nothing
    if not isinstance(value, list):
        rejected.append("age_groups_not_list")
        return ()
    groups: list[AgeGroup] = []
    for item in value:
        if not isinstance(item, str) or item not in AGE_GROUPS:
            rejected.append("age_group_unknown")
            continue
        if AgeGroup(item) not in groups:
            groups.append(AgeGroup(item))
    return tuple(groups)


def _parse_flag(value: object, name: str, rejected: list[str]) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        rejected.append(f"{name}_not_bool")
        return None
    return value


def _parse_languages(value: object, rejected: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()  # the prompt allows leaving this out when the page says nothing
    if not isinstance(value, list):
        rejected.append("languages_not_list")
        return ()
    languages: list[str] = []
    for item in value:
        if len(languages) >= MAX_LANGUAGES:
            rejected.append("languages_too_many")
            break
        if not isinstance(item, str):
            rejected.append("language_not_string")
            continue
        language = item.strip()
        if not language or len(language) > MAX_LANGUAGE_CHARS:
            rejected.append("language_out_of_range")
            continue
        if language not in languages:
            languages.append(language)
    return tuple(languages)


def claim_rejections(
    text: str, parsed: ParsedPage
) -> tuple[tuple[ServiceClaim, ...], tuple[str, ...]]:
    """The parsed claims whose quotes the page text actually supports, with a reason per
    dropped claim: ``claim_quote_not_in_page`` when the page text does not back the quote,
    ``claim_duplicate`` when the same service quotes the same words again. This is the only
    gate between a model and a trusted claim: an unsupported quote never reaches the
    store."""
    accepted: list[ServiceClaim] = []
    dropped: list[str] = []
    seen: set[tuple[Service, str]] = set()
    for claim in parsed.claims:
        if not quote_supported(text, claim.evidence_quote):
            dropped.append("claim_quote_not_in_page")
            continue
        key = (claim.service, collapse_whitespace(claim.evidence_quote))
        if key in seen:
            dropped.append("claim_duplicate")
            continue
        seen.add(key)
        accepted.append(claim)
    return tuple(accepted), tuple(dropped)


def accepted_claims(text: str, parsed: ParsedPage) -> tuple[ServiceClaim, ...]:
    """The claims ``claim_rejections`` accepts: quotes the page text supports, duplicates
    (same service quoting the same words again) dropped."""
    return claim_rejections(text, parsed)[0]


def merge_attributes(values: Sequence[PageAttributes]) -> PageAttributes:
    """One provider's attributes across pages.

    Age groups and languages union in first-seen order. A boolean is ``True`` when any page
    asserts it, else ``False`` when any page denies it, else ``None`` (no page stated it):
    evidence of delivery outranks a page that merely does not mention it.
    """
    age_groups: list[AgeGroup] = []
    languages: list[str] = []
    for value in values:
        for group in value.age_groups:
            if group not in age_groups:
                age_groups.append(group)
        for language in value.languages:
            if language not in languages:
                languages.append(language)

    def merged_flag(pick) -> bool | None:  # type: ignore[no-untyped-def]
        stated = [pick(value) for value in values if pick(value) is not None]
        if True in stated:
            return True
        return False if stated else None

    return PageAttributes(
        age_groups=tuple(age_groups),
        telehealth=merged_flag(lambda value: value.telehealth),
        home_visits=merged_flag(lambda value: value.home_visits),
        languages=tuple(languages),
    )


def estimated_tokens(text: str) -> int:
    """A deterministic lower-bound estimate of the tokens ``text`` costs: about four
    characters per token for English page text. Only used to decide whether a request can
    still fit in the token budget before it is sent."""
    return len(text) // 4 + 1


@dataclass(frozen=True)
class TokenBudget:
    """The per-run token budget the LLM adapter must respect. ``spend`` returns the budget
    after adding real usage; ``would_exceed`` answers whether a request estimated at
    ``input_tokens`` could still be sent without going over the limit."""

    limit: int
    spent: int = 0

    def spend(self, tokens: int) -> "TokenBudget":
        return TokenBudget(self.limit, self.spent + max(0, tokens))

    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def would_exceed(self, input_tokens: int) -> bool:
        return self.remaining() < max(0, input_tokens)


def page_priority(url: str) -> tuple[int, str, str]:
    """Sort key that puts shallow, short paths (home, services overview) before deep ones.

    A provider's service list lives on its home page and near-top service pages; contact
    and blog pages five clicks deep add little. Sorting by (path depth, path) is stable and
    needs no network knowledge.
    """
    path = urlsplit(url).path or "/"
    depth = sum(1 for part in path.split("/") if part)
    return (depth, path, url)


def select_pages(pages: Sequence[StoredPage], max_pages: int) -> tuple[StoredPage, ...]:
    """The pages one provider's extraction will spend model calls on: at most ``max_pages``,
    pages with identical text (same hash of collapsed text) called once, home-first order.
    Deterministic in the input order."""
    chosen: list[StoredPage] = []
    seen_texts: set[str] = set()
    for page in sorted(pages, key=lambda page: page_priority(page.url)):
        if len(chosen) >= max_pages:
            break
        fingerprint = collapse_whitespace(page.text)
        if fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        chosen.append(page)
    return tuple(chosen)


def page_excerpt(text: str, max_chars: int) -> str:
    """At most ``max_chars`` characters of page text, cut at a line boundary so the model
    never sees half a sentence. A shorter page is returned whole."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    if cut < MIN_EVIDENCE_CHARS:
        cut = max_chars
    return text[:cut].rstrip()


def known_services() -> frozenset[Service]:
    """Every service the taxonomy defines, for checks that a claimed service is real."""
    return frozenset(SERVICES)
