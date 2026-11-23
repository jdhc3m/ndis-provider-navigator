"""Pydantic response models for the HTTP API."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from ndis.application.ask import CombinedAnswer, ServicePrices, Step
from ndis.application.catalogue import PriceLimitResult, ServiceSearchResult
from ndis.application.pricing import RuleAnswer
from ndis.application.providers import RankedProvider
from ndis.domain.answer import PriceEntry, ServiceMention
from ndis.domain.catalogue import CATALOGUE_TITLE
from ndis.domain.entities import Provider, SupportItem
from ndis.domain.geo import Distance
from ndis.domain.intent import Intent
from ndis.domain.locality import LocalityResolution


class AddressOut(BaseModel):
    suburb: str | None
    state: str | None
    postcode: str | None


class DistanceOut(BaseModel):
    """Straight-line kilometres from the origin postcode's centroid, to one decimal, and
    whether it was measured from the provider's Maps listing (``exact``) or the centroid of
    its head-office postcode (``postcode_centroid``, approximate)."""

    km: float
    source: Literal["exact", "postcode_centroid"]

    @classmethod
    def from_distance(cls, distance: Distance | None) -> "DistanceOut | None":
        if distance is None:
            return None
        return cls(km=distance.shown_km, source=distance.source.value)


class ProviderOut(BaseModel):
    abn: str
    business_name: str
    legal_name: str
    head_office: AddressOut
    website: str | None
    website_domain: str | None
    registration_status: str
    registration_expires_on: date
    registration_groups: list[str]
    brisbane_area: bool
    distance: DistanceOut | None = None

    @classmethod
    def from_ranked(cls, ranked: RankedProvider) -> "ProviderOut":
        return cls.from_provider(ranked.provider, ranked.distance)

    @classmethod
    def from_provider(cls, provider: Provider, distance: Distance | None = None) -> "ProviderOut":
        return cls(
            abn=provider.abn,
            business_name=provider.business_name,
            legal_name=provider.legal_name,
            head_office=AddressOut(
                suburb=provider.head_office.suburb,
                state=provider.head_office.state,
                postcode=provider.head_office.postcode,
            ),
            website=provider.website,
            website_domain=provider.website_domain,
            registration_status=provider.registration_status,
            registration_expires_on=provider.registration_expires_on,
            registration_groups=[group.name for group in provider.registration_groups],
            brisbane_area=provider.brisbane_area,
            distance=DistanceOut.from_distance(distance),
        )


class ProviderPageOut(BaseModel):
    """One page of providers; ``distance_origin`` is the ``near`` postcode the distances
    were measured from, ``null`` when the page is in register order without distances.
    ``locality`` echoes the resolution when the query named a suburb."""

    items: list[ProviderOut]
    total: int
    limit: int
    offset: int
    distance_origin: str | None = None
    locality: "LocalityOut | None" = None


class LocalityCandidateOut(BaseModel):
    """One candidate a locality name may resolve to: the gazetteer spelling, its state
    and postcode."""

    name: str
    state: str
    postcode: str


class LocalityOut(BaseModel):
    """The outcome of resolving one locality name: the suburb the question wrote, the
    (state, postcode) it resolved to, or the unknown/ambiguous outcome with the full
    candidate list."""

    name: str
    outcome: Literal["resolved", "locality_unknown", "locality_ambiguous"]
    state: str | None = None
    postcode: str | None = None
    candidates: list[LocalityCandidateOut] = []

    @classmethod
    def from_resolution(cls, resolution: LocalityResolution) -> "LocalityOut":
        return cls(
            name=resolution.name,
            outcome=resolution.outcome.value,
            state=resolution.locality.state if resolution.locality else None,
            postcode=resolution.locality.postcode if resolution.locality else None,
            candidates=[
                LocalityCandidateOut(name=item.name, state=item.state, postcode=item.postcode)
                for item in resolution.candidates
            ],
        )


class PriceLimitsOut(BaseModel):
    """Limits per remoteness tier as exact decimal strings (``"82.57"``), ``null`` when none."""

    national: Decimal | None
    remote: Decimal | None
    very_remote: Decimal | None


class PriceLimitOut(BaseModel):
    item_number: str
    name: str
    registration_group_number: str
    registration_group_name: str
    unit: str
    quotable: bool
    legacy: bool
    start_date: date
    end_date: date | None
    state: str | None
    remoteness: str
    price_limit: Decimal | None
    limits: PriceLimitsOut
    source: str

    @classmethod
    def from_result(cls, result: PriceLimitResult) -> "PriceLimitOut":
        item = result.item
        return cls(
            item_number=item.item_number,
            name=item.name,
            registration_group_number=item.registration_group_number,
            registration_group_name=item.registration_group_name,
            unit=item.unit,
            quotable=item.quotable,
            legacy=item.legacy,
            start_date=item.start_date,
            end_date=item.end_date,
            state=result.state,
            remoteness=result.remoteness.value,
            price_limit=result.limit,
            limits=PriceLimitsOut(
                national=item.limits.national,
                remote=item.limits.remote,
                very_remote=item.limits.very_remote,
            ),
            source=CATALOGUE_TITLE,
        )


class SupportItemSummaryOut(BaseModel):
    """One support item in a search result: its number, name, unit and the published
    limits per remoteness tier (``null`` where none is published)."""

    item_number: str
    name: str
    unit: str
    quotable: bool
    legacy: bool
    limits: PriceLimitsOut

    @classmethod
    def from_item(cls, item: "SupportItem") -> "SupportItemSummaryOut":
        return cls(
            item_number=item.item_number,
            name=item.name,
            unit=item.unit,
            quotable=item.quotable,
            legacy=item.legacy,
            limits=PriceLimitsOut(
                national=item.limits.national,
                remote=item.limits.remote,
                very_remote=item.limits.very_remote,
            ),
        )


class SupportItemSearchOut(BaseModel):
    """The result of a service-phrase search over the support catalogue: the phrase,
    the total match count, the page (capped by ``limit``), and the source the rows come
    from. No model is involved, so the mapping can be checked without a key."""

    query: str
    total: int
    limit: int
    items: list[SupportItemSummaryOut]
    source: str

    @classmethod
    def from_result(cls, result: ServiceSearchResult, limit: int) -> "SupportItemSearchOut":
        return cls(
            query=result.phrase,
            total=result.total,
            limit=limit,
            items=[SupportItemSummaryOut.from_item(item) for item in result.items[:limit]],
            source=CATALOGUE_TITLE,
        )


class AskRuleIn(BaseModel):
    """One question about the NDIS pricing rules."""

    question: str = Field(min_length=3, max_length=500)


class RuleCitationOut(BaseModel):
    """The printed page of the pricing PDF and the verbatim span quoted from it."""

    page: int
    quote: str


class AskRuleOut(BaseModel):
    """An answer with page-verified citations, or a refusal with its reason."""

    question: str
    answer: str | None
    citations: list[RuleCitationOut]
    refused: bool
    reason: str | None
    top_similarity: float | None

    @classmethod
    def from_result(cls, result: RuleAnswer) -> "AskRuleOut":
        return cls(
            question=result.question,
            answer=result.answer,
            citations=[RuleCitationOut(page=page, quote=quote) for page, quote in result.citations],
            refused=result.refused,
            reason=result.reason,
            top_similarity=result.top_similarity,
        )


class AskIn(BaseModel):
    """One question to parse into an intent and answer deterministically."""

    question: str = Field(min_length=3, max_length=500)


class AskIntentFiltersOut(BaseModel):
    """The register filters the intent parse produced."""

    state: str | None
    postcode: str | None
    locality: str | None
    group: str | None
    status: str | None
    brisbane_area: bool | None


class AskPriceQueryOut(BaseModel):
    """The price query the intent parse produced: an item number the question itself
    stated, or the service phrase as the user wrote it. ``state`` is ``null`` when the
    question stated none (the national tier applies)."""

    item: str | None
    service: str | None
    state: str | None
    remoteness: str | None


class AskIntentOut(BaseModel):
    """The parsed intent, echoed for transparency, with its rejection reasons."""

    provider_filters: AskIntentFiltersOut | None
    price_query: AskPriceQueryOut | None
    rule_question: str | None
    decision_request: bool
    parse_rejections: list[str]

    @classmethod
    def from_intent(cls, intent: Intent, rejections: tuple[str, ...]) -> "AskIntentOut":
        filters = intent.provider_filters
        price = intent.price_query
        return cls(
            provider_filters=None
            if filters is None
            else AskIntentFiltersOut(
                state=filters.state,
                postcode=filters.postcode,
                locality=filters.locality,
                group=filters.group,
                status=filters.status,
                brisbane_area=filters.brisbane_area,
            ),
            price_query=None
            if price is None
            else AskPriceQueryOut(
                item=price.item,
                service=price.service,
                state=price.state,
                remoteness=price.remoteness,
            ),
            rule_question=intent.rule_question,
            decision_request=intent.decision_request,
            parse_rejections=list(rejections),
        )


class ServicePriceOut(BaseModel):
    """One catalogue row in a service-phrase answer: its number, name, unit and the
    limit that applies (``null`` when none is published, e.g. a quotable item)."""

    item_number: str
    name: str
    unit: str
    quotable: bool
    legacy: bool
    state: str | None
    remoteness: str
    price_limit: Decimal | None

    @classmethod
    def from_entry(cls, entry: PriceEntry) -> "ServicePriceOut":
        item = entry.item
        return cls(
            item_number=item.item_number,
            name=item.name,
            unit=item.unit,
            quotable=item.quotable,
            legacy=item.legacy,
            state=entry.state,
            remoteness=entry.remoteness.value,
            price_limit=entry.limit,
        )


class ServicePricesOut(BaseModel):
    """The price section for a service phrase: the phrase as the user wrote it, the
    state (``null`` when none was stated — the national tier applies), the total match
    count, and the capped rows shown. Every row comes from the support catalogue."""

    phrase: str
    state: str | None
    remoteness: str
    total: int
    items: list[ServicePriceOut]
    source: str

    @classmethod
    def from_result(cls, result: ServicePrices) -> "ServicePricesOut":
        return cls(
            phrase=result.phrase,
            state=result.state,
            remoteness=result.remoteness,
            total=result.total,
            items=[ServicePriceOut.from_entry(entry) for entry in result.entries],
            source=CATALOGUE_TITLE,
        )


class ServiceMentionOut(BaseModel):
    """One verified service: the taxonomy label, the page it was claimed on, the verbatim
    evidence quote and the date that page was fetched."""

    service: str
    url: str
    quote: str
    fetched_at: str | None

    @classmethod
    def from_mention(cls, mention: ServiceMention) -> "ServiceMentionOut":
        return cls(
            service=mention.label,
            url=mention.url,
            quote=mention.quote,
            fetched_at=mention.fetched_at,
        )


class AskProviderOut(ProviderOut):
    """One provider from the executed result page, with its verified service mentions and,
    when the question named a postcode, its distance from that postcode."""

    services: list[ServiceMentionOut]


class StepOut(BaseModel):
    """One stage of building the answer: its kind, short in/out summaries, wall time and,
    for the model call, tokens and cost (``null`` where not applicable or unpriced)."""

    kind: str
    input_summary: str
    output_summary: str
    ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None

    @classmethod
    def from_step(cls, step: Step) -> "StepOut":
        return cls(
            kind=step.kind,
            input_summary=step.input_summary,
            output_summary=step.output_summary,
            ms=step.ms,
            input_tokens=step.input_tokens,
            output_tokens=step.output_tokens,
            cost_usd=step.cost_usd,
        )


class AskOut(BaseModel):
    """The combined answer: the parsed intent, the executed sections, and the refusal
    fields. ``caveat`` carries the exact services sentence whenever services are used;
    ``steps`` lists the stages that built the answer, in order; ``locality`` discloses
    the suburb resolution when the question named one; ``service_prices`` carries the
    service phrase's catalogue price list when the question named a service instead of
    an item number."""

    question: str
    refused: bool
    reason: str | None
    explanation: str | None
    intent: AskIntentOut | None
    locality: LocalityOut | None = None
    providers: list[AskProviderOut]
    provider_total: int
    distance_origin: str | None
    price: PriceLimitOut | None
    price_error: str | None
    service_prices: ServicePricesOut | None = None
    rule: AskRuleOut | None
    caveat: str | None
    steps: list[StepOut]

    @classmethod
    def from_result(cls, result: CombinedAnswer) -> "AskOut":
        return cls(
            question=result.question,
            refused=result.refused,
            reason=result.reason,
            explanation=result.explanation,
            intent=None
            if result.intent is None
            else AskIntentOut.from_intent(result.intent, result.parse_rejections),
            locality=None
            if result.locality is None
            else LocalityOut.from_resolution(result.locality),
            providers=[
                AskProviderOut(
                    **ProviderOut.from_provider(
                        provider, result.distances.get(provider.abn)
                    ).model_dump(),
                    services=[
                        ServiceMentionOut.from_mention(mention)
                        for mention in result.services.get(provider.abn, ())
                    ],
                )
                for provider in result.providers
            ],
            provider_total=result.provider_total,
            distance_origin=result.distance_origin,
            price=None if result.price is None else PriceLimitOut.from_result(result.price),
            price_error=result.price_error,
            service_prices=None
            if result.service_prices is None
            else ServicePricesOut.from_result(result.service_prices),
            rule=None if result.rule is None else AskRuleOut.from_result(result.rule),
            caveat=result.caveat,
            steps=[StepOut.from_step(step) for step in result.steps],
        )


class TracingOut(BaseModel):
    """Whether tracing is configured and, if so, where the traces can be viewed."""

    configured: bool
    url: str | None


class MetaOut(BaseModel):
    """What the page needs to label itself truthfully: the configured model ids (empty
    strings when unset), the data snapshots and sources, and the tracing status. Nothing
    here is hard-coded in the front end."""

    llm_model: str
    embed_model: str
    llm_configured: bool
    register_snapshot_date: date | None
    catalogue_title: str
    financial_year: str
    pricing_document: str
    rule_chunk_strategy: str
    tracing: TracingOut
