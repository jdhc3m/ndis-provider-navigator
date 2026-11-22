"""FastAPI application exposing the provider register, price limits, rule answers, the
combined /ask endpoint and the /meta facts the web page labels itself with; when the
front end has been built it is served from ``/`` as static files."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ndis.adapters.tracing import langfuse_enabled, tracing_url
from ndis.api.dependencies import (
    ask_rule_use_case,
    ask_use_case,
    cors_origins,
    get_price_limit_use_case,
    list_providers_use_case,
    locality_resolver,
    register_snapshot_date,
    search_support_items_use_case,
    web_dist_dir,
)
from ndis.api.schemas import (
    AskIn,
    AskOut,
    AskRuleIn,
    AskRuleOut,
    LocalityCandidateOut,
    LocalityOut,
    MetaOut,
    PriceLimitOut,
    ProviderOut,
    ProviderPageOut,
    SupportItemSearchOut,
    TracingOut,
)
from ndis.application.ask import Ask as AskCombined
from ndis.application.catalogue import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    GetPriceLimit,
    PriceLimitQuery,
    SearchSupportItems,
    UnknownSupportItemError,
)
from ndis.application.extraction import BudgetExhausted, LlmError
from ndis.application.pricing import AskRule, EmbeddingError
from ndis.application.providers import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidQueryError,
    ListProviders,
    LocalityResolver,
    ProviderQuery,
)
from ndis.config import embed_model, llm_configured, llm_model, rule_chunk_strategy
from ndis.domain.catalogue import CATALOGUE_TITLE, FINANCIAL_YEAR
from ndis.domain.locality import (
    REJECTION_LOCALITY_POSTCODE_MISMATCH,
    LocalityOutcome,
    LocalityResolution,
)
from ndis.domain.pricing import PRICING_DOCUMENT_TITLE


def _locality_422(
    resolution: LocalityResolution, reason: str, postcode: str | None = None
) -> HTTPException:
    """The 422 an unresolved locality name answers with: the machine reason, the full
    candidate list (empty for an unknown name) and a readable message."""
    candidates = [
        LocalityCandidateOut(name=item.name, state=item.state, postcode=item.postcode)
        for item in resolution.candidates
    ]
    if (
        reason == REJECTION_LOCALITY_POSTCODE_MISMATCH
        and resolution.locality is not None
        and postcode is not None
    ):
        point = resolution.locality
        message = (
            f"the locality {resolution.name!r} is {point.state} {point.postcode}, "
            f"not the stated postcode {postcode}"
        )
    elif reason == LocalityOutcome.AMBIGUOUS.value:
        message = (
            f"the locality {resolution.name!r} matches several postcodes; "
            "name the state or ask again with one candidate's postcode"
        )
    elif resolution.locality is not None:
        # A resolved locality never deserves the "not in the gazetteer" message, however
        # the reason got here.
        point = resolution.locality
        message = (
            f"the locality {resolution.name!r} is {point.state} {point.postcode}; "
            "ask again with that postcode"
        )
    else:
        message = f"the locality {resolution.name!r} is not in the loaded gazetteer"
    return HTTPException(
        status_code=422,
        detail={
            "reason": reason,
            "message": message,
            "candidates": [candidate.model_dump() for candidate in candidates],
        },
    )


def create_app() -> FastAPI:
    app = FastAPI(title="NDIS Provider Navigator", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins()),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    # Registered by call rather than decorator so the handler is a plain module-level
    # function that tests import and reference by name (scripts/check_no_stubs.py).
    app.get("/providers", response_model=ProviderPageOut)(list_providers)
    app.get("/price-limits", response_model=PriceLimitOut)(get_price_limit)
    app.get("/support-items", response_model=SupportItemSearchOut)(search_support_items)
    app.post("/ask-rule", response_model=AskRuleOut)(ask_rule)
    app.post("/ask", response_model=AskOut)(ask)
    app.get("/meta", response_model=MetaOut)(meta)
    # The built front end, mounted last so every API route above wins; absent until
    # `pnpm --dir web build` has run, in which case `/` stays 404.
    dist = web_dist_dir()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def list_providers(
    use_case: Annotated[ListProviders, Depends(list_providers_use_case)],
    localities: Annotated[LocalityResolver, Depends(locality_resolver)],
    state: str | None = None,
    postcode: str | None = None,
    locality: str | None = None,
    group: str | None = None,
    status: str | None = None,
    brisbane_area: bool | None = None,
    near: str | None = None,
    max_km: Annotated[float | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProviderPageOut:
    """List registered providers nationally, filtered and paginated. With ``near`` (a
    postcode) the whole matched set is ranked by straight-line distance from that
    postcode's centroid before the page is cut; ``max_km`` (inclusive) requires ``near``.
    ``locality`` (a suburb name) is resolved to a ``(state, postcode)`` in code; an
    unknown or ambiguous name is a 422 with the reason and the candidates."""
    resolution: LocalityOut | None = None
    if locality is not None:
        resolved = localities(locality, state=state, brisbane_area=brisbane_area)
        if resolved.outcome is not LocalityOutcome.RESOLVED or resolved.locality is None:
            raise _locality_422(resolved, resolved.outcome.value)
        if postcode is not None and postcode != resolved.locality.postcode:
            raise _locality_422(resolved, REJECTION_LOCALITY_POSTCODE_MISMATCH, postcode)
        postcode = resolved.locality.postcode
        resolution = LocalityOut.from_resolution(resolved)
    query = ProviderQuery(
        state=state,
        postcode=postcode,
        group=group,
        status=status,
        brisbane_area=brisbane_area,
        near=near,
        max_km=max_km,
        limit=limit,
        offset=offset,
    )
    try:
        page = use_case(query)
    except InvalidQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProviderPageOut(
        items=[ProviderOut.from_ranked(ranked) for ranked in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        distance_origin=page.distance_origin,
        locality=resolution,
    )


def get_price_limit(
    use_case: Annotated[GetPriceLimit, Depends(get_price_limit_use_case)],
    item: str,
    state: str,
    remoteness: str | None = None,
) -> PriceLimitOut:
    """Deterministic 2026-27 price limit of one support item; remoteness defaults to national."""
    try:
        result = use_case(PriceLimitQuery(item=item, state=state, remoteness=remoteness))
    except InvalidQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnknownSupportItemError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PriceLimitOut.from_result(result)


def search_support_items(
    use_case: Annotated[SearchSupportItems, Depends(search_support_items_use_case)],
    q: str,
    limit: Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)] = DEFAULT_SEARCH_LIMIT,
) -> SupportItemSearchOut:
    """Match a service phrase ("occupational therapy", "OT") against the support
    catalogue in code — no model, so no key is needed — and return the deterministic
    match list (base items before claim-type variants, current before legacy, then item
    number) with its total match count."""
    try:
        result = use_case(q)
    except InvalidQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SupportItemSearchOut.from_result(result, limit)


def ask_rule(
    use_case: Annotated[AskRule, Depends(ask_rule_use_case)],
    payload: AskRuleIn,
) -> AskRuleOut:
    """Answer a question about the pricing rules with page-verified citations, or refuse."""
    try:
        result = use_case(payload.question)
    except (LlmError, EmbeddingError, BudgetExhausted) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AskRuleOut.from_result(result)


def ask(
    use_case: Annotated[AskCombined, Depends(ask_use_case)],
    payload: AskIn,
) -> AskOut:
    """Parse one question into an intent, execute it deterministically, and return the
    combined answer — or a refusal with its reason."""
    try:
        result = use_case(payload.question)
    except (LlmError, EmbeddingError, BudgetExhausted) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return AskOut.from_result(result)


def meta() -> MetaOut:
    """The facts the page labels itself with: model ids and data snapshots from the
    configuration and the filesystem, never from the front end's code."""
    return MetaOut(
        llm_model=llm_model(),
        embed_model=embed_model(),
        llm_configured=llm_configured(),
        register_snapshot_date=register_snapshot_date(),
        catalogue_title=CATALOGUE_TITLE,
        financial_year=FINANCIAL_YEAR,
        pricing_document=PRICING_DOCUMENT_TITLE,
        rule_chunk_strategy=rule_chunk_strategy(),
        tracing=TracingOut(configured=langfuse_enabled(), url=tracing_url()),
    )


app = create_app()
