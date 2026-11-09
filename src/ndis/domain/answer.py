"""The composed answer document and its output guard.

Everything the answer contains was executed deterministically — the provider list from
SQL, service mentions from the extraction cache — except the rule answer's prose, which
the ticket 06 pipeline already grounds. The guard is the last line of defence, as pure
domain code: a provider may only appear when it is in the executed result set, a service
may only be attached when its evidence quote is a verbatim span of the cited page's
stored text, and the services caveat must appear exactly whenever services are used.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ndis.domain.entities import Provider
from ndis.domain.extraction import quote_supported
from ndis.domain.taxonomy import Service, entry_of

# The exact sentence the output must carry whenever services from provider websites are
# part of the answer. It is pinned here so every layer shares one spelling of it.
CAVEAT_SERVICES = (
    "the register does not record profession; services come from the provider's website"
)

# One provider's service list in the answer is capped so one loud page cannot flood it.
MAX_SERVICE_MENTIONS = 12


@dataclass(frozen=True)
class ClaimedService:
    """A service claim read back from the extraction cache: the service, the page URL it
    was claimed from, the evidence quote, and the model's confidence. Never trusted until
    the quote survives ``quote_supported`` against the stored page text."""

    service: Service
    url: str
    quote: str
    confidence: float = 0.0


@dataclass(frozen=True)
class ServiceMention:
    """One verified service in the answer, with the evidence the disclosure needs."""

    service: Service
    url: str
    quote: str
    fetched_at: str | None = None

    @property
    def label(self) -> str:
        """The human label of the taxonomy entry, e.g. ``Occupational therapy``."""
        return entry_of(self.service).label


@dataclass(frozen=True)
class ProviderEntry:
    """One provider as named in the answer, with its verified service mentions."""

    abn: str
    name: str
    services: tuple[ServiceMention, ...] = ()


@dataclass(frozen=True)
class AnswerDocument:
    """The whole answer as the output guard sees it."""

    providers: tuple[ProviderEntry, ...] = ()
    caveat: str | None = None


@dataclass(frozen=True)
class GuardResult:
    """The guard's verdict: valid only when no violation was found."""

    valid: bool
    violations: tuple[str, ...] = ()


def fold_claims(claims: Sequence[ClaimedService]) -> tuple[ClaimedService, ...]:
    """One claim per (url, service), the most confident winning, in a deterministic order.

    The extraction cache keeps every accepted claim; the answer shows each page's claim
    once. Ties keep the first seen, so the result is stable in the input order."""
    winners: dict[tuple[str, Service], ClaimedService] = {}
    for claim in claims:
        key = (claim.url, claim.service)
        current = winners.get(key)
        if current is None or claim.confidence > current.confidence:
            winners[key] = claim
    return tuple(winners[key] for key in sorted(winners, key=lambda key: (key[0], key[1].value)))


def mentions_for_provider(
    claims: Sequence[ClaimedService], page_texts: Mapping[str, str]
) -> tuple[ServiceMention, ...]:
    """The service mentions one provider's answer entry may carry: only claims whose
    evidence quote is a verbatim span of the cited page's stored text, capped at
    ``MAX_SERVICE_MENTIONS``. A claim with no stored page text is never evidence."""
    mentions: list[ServiceMention] = []
    for claim in fold_claims(claims):
        text = page_texts.get(claim.url, "")
        if not quote_supported(text, claim.quote):
            continue
        mentions.append(ServiceMention(claim.service, claim.url, claim.quote))
        if len(mentions) >= MAX_SERVICE_MENTIONS:
            break
    return tuple(mentions)


def uses_services(document: AnswerDocument) -> bool:
    """True when any provider entry in the document carries a service mention."""
    return any(entry.services for entry in document.providers)


def check_answer(
    document: AnswerDocument,
    results: Sequence[Provider],
    page_texts: Mapping[tuple[str, str], str],
) -> GuardResult:
    """The output guard over the composed answer.

    - Every entry's ABN must be in the executed result set and its name must be that
      provider's business name (``provider_not_in_results`` / ``provider_name_mismatch``).
    - Every service mention's quote must be a verbatim span of the cited page's stored
      text (``evidence_page_missing`` / ``evidence_quote_not_in_page``).
    - The exact services caveat must be present whenever any entry uses services, and
      absent otherwise (``caveat_missing`` / ``caveat_unneeded``).
    """
    violations: list[str] = []
    allowed = {provider.abn: provider for provider in results}
    for entry in document.providers:
        provider = allowed.get(entry.abn)
        if provider is None:
            violations.append("provider_not_in_results")
            continue
        if entry.name != provider.business_name:
            violations.append("provider_name_mismatch")
        for mention in entry.services:
            text = page_texts.get((entry.abn, mention.url), "")
            if not text:
                violations.append("evidence_page_missing")
            elif not quote_supported(text, mention.quote):
                violations.append("evidence_quote_not_in_page")
    if uses_services(document) and document.caveat != CAVEAT_SERVICES:
        violations.append("caveat_missing")
    if not uses_services(document) and document.caveat is not None:
        violations.append("caveat_unneeded")
    return GuardResult(not violations, tuple(violations))
