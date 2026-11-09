"""Ports and the use case that turns stored page text into provider service profiles.

The LLM is one port behind this use case, and nothing it says is trusted here either:
every model answer goes through the deterministic parser and evidence gate in the domain
before a claim is kept, and a provider's profile is only cached when every selected page
of its run succeeded, so a failed page is retried on the next run instead of being
forgotten.
"""

from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from ndis.domain.extraction import (
    PageAttributes,
    PageClaim,
    ProviderExtraction,
    StoredPage,
    TokenBudget,
    claim_rejections,
    merge_attributes,
    page_excerpt,
    parse_extraction,
    select_pages,
)
from ndis.domain.taxonomy import TAXONOMY, Service


class LlmError(RuntimeError):
    """The LLM endpoint failed for one request in a way no retry could fix."""


class BudgetExhausted(RuntimeError):
    """The run's token budget is spent; the caller should stop and keep what it has."""


@dataclass(frozen=True)
class LlmRequest:
    """One chat completion: a system prompt, a user prompt and an output cap."""

    system: str
    user: str
    max_output_tokens: int = 2048


@dataclass(frozen=True)
class LlmResult:
    """The model's answer text and the token usage the endpoint reported (or, when it
    reports none, the adapter's deterministic estimate)."""

    text: str
    input_tokens: int
    output_tokens: int


class LlmClient(Protocol):
    """One structured-output completion against a chat endpoint, with its own pacing,
    retries and per-run token budget."""

    def complete(self, request: LlmRequest) -> LlmResult:
        """The answer text; raises ``LlmError`` on permanent failures and
        ``BudgetExhausted`` when the run's budget cannot cover the request."""
        ...

    @property
    def budget(self) -> TokenBudget:
        """The budget this client has spent so far, for run summaries."""
        ...


class PageEvidenceSource(Protocol):
    """Read port over the stored, ``ok`` page text of every provider."""

    def ok_pages(self) -> tuple[StoredPage, ...]:
        """Every stored page with text, ordered by provider key then URL."""
        ...


class ExtractionStore(Protocol):
    """Persistence port for provider profiles: the private cache of record plus whatever
    public-safe projection the adapter keeps."""

    def cached_provider_keys(self) -> frozenset[str]:
        """Provider keys whose extraction is already complete."""
        ...

    def save(self, profile: ProviderExtraction) -> None:
        """Store one complete provider profile."""
        ...

    def load(self, provider_key: str) -> ProviderExtraction | None:
        """The stored profile of one provider, or ``None``."""
        ...


class BaselineSource(Protocol):
    """Read port over the regex baseline of the earlier crawl, keyed by place id."""

    def baseline(self) -> Mapping[str, frozenset[str]]:
        """Place id → the slugs the regex matcher hit on that place."""
        ...


@dataclass(frozen=True)
class ExtractionCounts:
    """What one run did, for the script's log line and the report."""

    providers: int
    cached: int
    extracted: int
    failed: int
    pages_sent: int
    claims_accepted: int
    claims_rejected: int
    rejections: Mapping[str, int] = field(default_factory=dict)
    budget_exhausted: bool = False

    def summary(self) -> str:
        """One log line with every count."""
        details = ", ".join(f"{key}={count}" for key, count in sorted(self.rejections.items()))
        return (
            f"{self.providers} providers, {self.cached} already cached, {self.extracted} "
            f"extracted, {self.failed} failed; {self.pages_sent} pages sent; "
            f"claims {self.claims_accepted} accepted, {self.claims_rejected} unsupported; "
            f"rejections {details or 'none'}"
            + ("; BUDGET EXHAUSTED" if self.budget_exhausted else "")
        )


MAX_PAGES_PER_PROVIDER = 8
MAX_PAGE_CHARS = 12_000

EXTRACTION_SYSTEM_PROMPT = """\
You are an information-extraction engine. You read one page of an Australian NDIS
provider's website and answer with JSON only - no prose, no markdown fence.

Rules:
- Claim a service only when this page's text shows the provider OFFERS it to participants.
  Marketing prose about the NDIS in general, referral notes and job ads are not evidence.
- Every claim needs "evidence_quote": a passage copied VERBATIM from the page text,
  between 25 and 400 characters. Copy the exact characters, including original spelling
  and punctuation; do not fix, translate, shorten to a heading or paraphrase anything.
- "confidence" is a number from 0 to 1: 1.0 when the page plainly states the service,
  0.5 when the wording strongly implies it, below that for anything weaker.
- Report "age_groups", "telehealth", "home_visits" and "languages" only when the page
  itself states them; leave a field out or empty when it does not.
"""


def _taxonomy_lines() -> str:
    return "\n".join(
        f"- {entry.service.value}: {entry.label} - {entry.description}" for entry in TAXONOMY
    )


def page_prompt(page_text: str) -> str:
    """The user prompt for one page: the service catalogue, the JSON shape, the page text."""
    return f"""\
Services you may claim (use the exact key on the left):
{_taxonomy_lines()}

Answer with one JSON object of this exact shape:
{{"claims": [{{"service": "<key>", "evidence_quote": "<verbatim 25-400 chars>",
  "confidence": 0.0}}], "age_groups": ["early_childhood"|"school_age"|"adults"|"older_adults"],
 "telehealth": true|false, "home_visits": true|false, "languages": ["<language>", ...]}}

Page text (verbatim, may be truncated):
<<<PAGE
{page_text}
PAGE>>>"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Tally:
    """Mutable run counters; frozen ``ExtractionCounts`` is built from this at the end."""

    def __init__(self, providers: int, cached: int) -> None:
        self.providers = providers
        self.cached = cached
        self.extracted = 0
        self.failed = 0
        self.pages_sent = 0
        self.claims_accepted = 0
        self.claims_rejected = 0
        self.rejections: dict[str, int] = {}
        self.budget_exhausted = False


class _Outcome(StrEnum):
    """How one provider's extraction ended."""

    COMPLETED = "completed"
    PAGE_FAILED = "page_failed"
    BUDGET_STOPPED = "budget_stopped"


class ExtractServices:
    """Use case: extract service claims from every uncached provider's stored pages.

    ``provider_keys`` restricts the run to named providers, ``limit`` caps how many
    uncached providers are processed this run. A provider is processed only when every
    selected page answers: one failed page leaves the whole provider uncached, so a rerun
    redoes it as a unit. An exhausted budget stops the run after the current provider.
    """

    def __init__(
        self,
        source: PageEvidenceSource,
        client: LlmClient,
        store: ExtractionStore,
        *,
        max_pages: int = MAX_PAGES_PER_PROVIDER,
        max_page_chars: int = MAX_PAGE_CHARS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._source = source
        self._client = client
        self._store = store
        self._max_pages = max_pages
        self._max_page_chars = max_page_chars
        self._clock = clock

    def __call__(
        self, provider_keys: Sequence[str] | None = None, *, limit: int | None = None
    ) -> ExtractionCounts:
        pages_by_key: dict[str, list[StoredPage]] = {}
        for page in self._source.ok_pages():
            pages_by_key.setdefault(page.provider_key, []).append(page)

        cached = self._store.cached_provider_keys()
        keys = sorted(pages_by_key) if provider_keys is None else sorted(set(provider_keys))
        tally = _Tally(
            providers=sum(1 for key in keys if pages_by_key.get(key)),
            cached=sum(1 for key in keys if key in cached),
        )
        for key in keys:
            if limit is not None and tally.extracted >= limit:
                break
            if key not in pages_by_key or key in cached:
                continue
            outcome = self._extract(key, pages_by_key[key], tally)
            if outcome is _Outcome.COMPLETED:
                tally.extracted += 1
            elif outcome is _Outcome.PAGE_FAILED:
                tally.failed += 1
            else:
                break
        return ExtractionCounts(
            providers=tally.providers,
            cached=tally.cached,
            extracted=tally.extracted,
            failed=tally.failed,
            pages_sent=tally.pages_sent,
            claims_accepted=tally.claims_accepted,
            claims_rejected=tally.claims_rejected,
            rejections=tally.rejections,
            budget_exhausted=tally.budget_exhausted,
        )

    def _extract(self, key: str, pages: Sequence[StoredPage], tally: _Tally) -> _Outcome:
        """One provider's extraction, page by page; the profile is cached only on success."""
        claims: list[PageClaim] = []
        attributes: list[PageAttributes] = []
        input_tokens = output_tokens = 0
        for page in select_pages(pages, self._max_pages):
            excerpt = page_excerpt(page.text, self._max_page_chars)
            try:
                result = self._client.complete(
                    LlmRequest(EXTRACTION_SYSTEM_PROMPT, page_prompt(excerpt))
                )
            except BudgetExhausted:
                tally.budget_exhausted = True
                return _Outcome.BUDGET_STOPPED
            except LlmError:
                return _Outcome.PAGE_FAILED
            tally.pages_sent += 1
            input_tokens += result.input_tokens
            output_tokens += result.output_tokens
            parsed = parse_extraction(result.text)
            if "not_json" in parsed.rejected or "not_object" in parsed.rejected:
                return _Outcome.PAGE_FAILED
            for reason in parsed.rejected:
                tally.rejections[reason] = tally.rejections.get(reason, 0) + 1
            accepted, dropped = claim_rejections(excerpt, parsed)
            for reason in dropped:
                tally.rejections[reason] = tally.rejections.get(reason, 0) + 1
            claims.extend(PageClaim(page.url, claim) for claim in accepted)
            tally.claims_accepted += len(accepted)
            tally.claims_rejected += dropped.count("claim_quote_not_in_page")
            attributes.append(parsed.attributes)
        self._store.save(
            ProviderExtraction(
                provider_key=key,
                claims=tuple(claims),
                attributes=merge_attributes(attributes),
                extracted_at=self._clock(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        return _Outcome.COMPLETED


@dataclass(frozen=True)
class PrecisionRecall:
    """Confusion counts of one system on one service over the labelled sites."""

    tp: int
    fp: int
    fn: int

    def precision(self) -> float | None:
        """None when the system never predicted the service at all."""
        return None if self.tp + self.fp == 0 else self.tp / (self.tp + self.fp)

    def recall(self) -> float | None:
        """None when no labelled site truly offers the service."""
        return None if self.tp + self.fn == 0 else self.tp / (self.tp + self.fn)


def score_service(
    truth: Sequence[frozenset[Service]],
    predicted: Sequence[frozenset[Service]],
) -> PrecisionRecall:
    """Precision/recall of one system on one service across sites, as parallel site sets."""
    tp = sum(1 for real, guess in zip(truth, predicted, strict=True) if guess and real)
    fp = sum(1 for real, guess in zip(truth, predicted, strict=True) if guess and not real)
    fn = sum(1 for real, guess in zip(truth, predicted, strict=True) if real and not guess)
    return PrecisionRecall(tp, fp, fn)


def baseline_prediction(slugs: AbstractSet[str]) -> frozenset[Service]:
    """The services the regex baseline claims for a place: every taxonomy service one of
    the place's hit slugs bridges to."""
    from ndis.domain.taxonomy import services_for_slug

    return frozenset(service for slug in slugs for service in services_for_slug(slug))
