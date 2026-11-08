"""Entities and value objects for the NDIS Commission provider register."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RegistrationGroup:
    """A registration group as named in the register, e.g. ``Assist-Life Stage, Transition``."""

    name: str


@dataclass(frozen=True)
class AddressParts:
    """Locality parts of an Australian address; each part is ``None`` when unknown."""

    suburb: str | None = None
    state: str | None = None
    postcode: str | None = None


@dataclass(frozen=True)
class Provider:
    """One registered provider per ABN."""

    abn: str
    business_name: str
    legal_name: str
    head_office: AddressParts
    website: str | None
    website_domain: str | None
    registration_status: str
    registration_expires_on: date
    registration_groups: tuple[RegistrationGroup, ...]
    brisbane_area: bool


@dataclass(frozen=True)
class Outlet:
    """One register row: an outlet (possibly blank) of a provider."""

    provider_abn: str
    name: str | None
    address_raw: str | None
    address: AddressParts
    phone: str | None
    brisbane_area: bool
