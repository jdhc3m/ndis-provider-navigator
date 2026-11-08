"""Pydantic response models for the HTTP API."""

from datetime import date

from pydantic import BaseModel

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
