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
    register_snapshot_date,
    web_dist_dir,
)
from ndis.api.schemas import (
    AskIn,
    AskOut,
    AskRuleIn,
    AskRuleOut,
    MetaOut,
    PriceLimitOut,
    ProviderOut,
    ProviderPageOut,
    TracingOut,
)
from ndis.application.ask import Ask as AskCombined
from ndis.application.catalogue import GetPriceLimit, PriceLimitQuery, UnknownSupportItemError
from ndis.application.extraction import BudgetExhausted, LlmError
from ndis.application.pricing import AskRule, EmbeddingError
from ndis.application.providers import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidQueryError,
    ListProviders,
    ProviderQuery,
)
from ndis.config import embed_model, llm_configured, llm_model, rule_chunk_strategy
from ndis.domain.catalogue import CATALOGUE_TITLE, FINANCIAL_YEAR
from ndis.domain.pricing import PRICING_DOCUMENT_TITLE


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
    state: str | None = None,
    postcode: str | None = None,
    group: str | None = None,
    status: str | None = None,
    brisbane_area: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProviderPageOut:
    """List registered providers nationally, filtered and paginated."""
    query = ProviderQuery(
        state=state,
        postcode=postcode,
        group=group,
        status=status,
        brisbane_area=brisbane_area,
        limit=limit,
        offset=offset,
    )
    try:
        page = use_case(query)
    except InvalidQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ProviderPageOut(
        items=[ProviderOut.from_provider(provider) for provider in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
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
