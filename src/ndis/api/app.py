"""FastAPI application exposing the provider register."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query

from ndis.api.dependencies import list_providers_use_case
from ndis.api.schemas import ProviderOut, ProviderPageOut
from ndis.application.providers import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidQueryError,
    ListProviders,
    ProviderQuery,
)


def create_app() -> FastAPI:
    app = FastAPI(title="NDIS Provider Navigator", version="0.1.0")
    app.get("/providers", response_model=ProviderPageOut)(list_providers)
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


app = create_app()
