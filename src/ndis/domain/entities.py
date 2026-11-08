"""Entities and value objects for the provider register and the support catalogue."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


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


class Remoteness(StrEnum):
    """Price-limit tier of the support catalogue; ``NATIONAL`` is the standard, non-remote tier."""

    NATIONAL = "national"
    REMOTE = "remote"
    VERY_REMOTE = "very_remote"


@dataclass(frozen=True)
class PriceLimits:
    """Maximum prices of one support item per remoteness tier; ``None`` when none is published."""

    national: Decimal | None
    remote: Decimal | None
    very_remote: Decimal | None

    def for_remoteness(self, remoteness: Remoteness) -> Decimal | None:
        if remoteness is Remoteness.REMOTE:
            return self.remote
        if remoteness is Remoteness.VERY_REMOTE:
            return self.very_remote
        return self.national


@dataclass(frozen=True)
class SupportItem:
    """One row of the NDIS Support Catalogue, identified by its support item number.

    ``quotable`` items have no published limit. ``end_date`` is ``None`` when the catalogue
    gives no end (``99991231``); ``legacy`` marks rows from the ``Legacy Support Items`` sheet.
    """

    item_number: str
    name: str
    registration_group_number: str
    registration_group_name: str
    unit: str
    quotable: bool
    start_date: date
    end_date: date | None
    limits: PriceLimits
    legacy: bool
