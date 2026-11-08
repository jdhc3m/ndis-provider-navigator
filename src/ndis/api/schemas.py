"""Pydantic response models for the HTTP API."""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from ndis.application.catalogue import PriceLimitResult
from ndis.domain.catalogue import CATALOGUE_TITLE
from ndis.domain.entities import Provider


class AddressOut(BaseModel):
    suburb: str | None
    state: str | None
    postcode: str | None


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

    @classmethod
    def from_provider(cls, provider: Provider) -> "ProviderOut":
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
        )


class ProviderPageOut(BaseModel):
    items: list[ProviderOut]
    total: int
    limit: int
    offset: int


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
    state: str
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
