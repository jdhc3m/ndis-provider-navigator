"""FastAPI application exposing the provider register, price limits and rule answers."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from ndis.api.dependencies import (
    ask_rule_use_case,
    get_price_limit_use_case,
    list_providers_use_case,
)
from ndis.api.schemas import AskRuleIn, AskRuleOut, PriceLimitOut, ProviderOut, ProviderPageOut
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


def create_app() -> FastAPI:
    app = FastAPI(title="NDIS Provider Navigator", version="0.1.0")
    # Registered by call rather than decorator so the handler is a plain module-level
    # function that tests import and reference by name (scripts/check_no_stubs.py).
    app.get("/providers", response_model=ProviderPageOut)(list_providers)
    app.get("/price-limits", response_model=PriceLimitOut)(get_price_limit)
    app.post("/ask-rule", response_model=AskRuleOut)(ask_rule)
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


app = create_app()
