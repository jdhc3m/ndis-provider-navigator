"""Pure parsing rules for rows of the NDIS Commission provider register CSV."""

from collections.abc import Mapping
from datetime import date, datetime
from urllib.parse import urlsplit

from ndis.domain.entities import AddressParts, Outlet, Provider, RegistrationGroup

COLUMN_BUSINESS_NAME = "Provider business name"
COLUMN_LEGAL_NAME = "Legal name"
COLUMN_ABN = "ABN"
COLUMN_HEAD_OFFICE = "Head office address"
COLUMN_WEBSITE = "Website"
COLUMN_STATUS = "Registration status"
COLUMN_EXPIRY = "Period of registration in force until"
COLUMN_GROUPS = "Approved registration groups"
COLUMN_CONDITIONS = "Conditions of registration"
COLUMN_OUTLET_NAME = "Outlet name"
COLUMN_OUTLET_ADDRESS = "Outlet address"
COLUMN_OUTLET_PHONE = "Outlet phone"

REGISTER_COLUMNS = (
    COLUMN_BUSINESS_NAME,
    COLUMN_LEGAL_NAME,
    COLUMN_ABN,
    COLUMN_HEAD_OFFICE,
    COLUMN_WEBSITE,
    COLUMN_STATUS,
    COLUMN_EXPIRY,
    COLUMN_GROUPS,
    COLUMN_CONDITIONS,
    COLUMN_OUTLET_NAME,
    COLUMN_OUTLET_ADDRESS,
    COLUMN_OUTLET_PHONE,
)

STATES = frozenset({"ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"})
REGISTRATION_STATUSES = frozenset({"Approved", "Revoked"})
COUNTRY_SUFFIX = "AU"
BRISBANE_POSTCODE_RANGES = ((4000, 4207), (4300, 4305))


class RegisterRowError(ValueError):
    """A register row that cannot be turned into entities; ``reason`` is a short slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_brisbane_postcode(postcode: str | None) -> bool:
    """True for postcodes in the Brisbane area ranges (4000–4207 and 4300–4305)."""
    if postcode is None or not postcode.isdigit():
        return False
    value = int(postcode)
    return any(low <= value <= high for low, high in BRISBANE_POSTCODE_RANGES)


def parse_address_tail(address: str) -> AddressParts:
    """Extract ``SUBURB, STATE, POSTCODE`` from an address ending ``..., STATE, POSTCODE, AU``.

    Works for both head office addresses (``CARDIFF, NSW, 2285, AU``) and full outlet
    street addresses. A bare ``AU`` or an empty string yields all-``None`` parts; a
    malformed state or postcode is dropped rather than rejecting the row.
    """
    parts = [part.strip() for part in address.split(",")]
    if len(parts) < 4 or parts[-1].upper() != COUNTRY_SUFFIX:
        return AddressParts()
    suburb, state, postcode = parts[-4], parts[-3].upper(), parts[-2]
    return AddressParts(
        suburb=suburb or None,
        state=state if state in STATES else None,
        postcode=postcode if postcode.isdigit() and len(postcode) == 4 else None,
    )


def website_domain(website: str | None) -> str | None:
    """Reduce a website URL to its lower-case host without scheme, ``www.``, port or path."""
    if website is None or not website.strip():
        return None
    candidate = website.strip()
    if "://" not in candidate:
        candidate = "//" + candidate
    host = urlsplit(candidate).hostname or ""
    host = host.strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[len("www.") :]
    if not host or "." not in host or any(char.isspace() for char in host):
        return None
    return host


def split_registration_groups(groups: str) -> tuple[RegistrationGroup, ...]:
    """Split the ``;``-separated group list; group names may contain commas."""
    seen: dict[str, RegistrationGroup] = {}
    for raw in groups.split(";"):
        name = raw.strip()
        if name and name not in seen:
            seen[name] = RegistrationGroup(name)
    return tuple(seen.values())


def parse_register_date(value: str) -> date:
    """Parse ``10 November 2028`` into a date."""
    return datetime.strptime(value.strip(), "%d %B %Y").date()


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_register_row(row: Mapping[str, str | None]) -> tuple[Provider, Outlet]:
    """Turn one register CSV row into a Provider and the Outlet described by that row.

    Raises ``RegisterRowError`` with a reason slug when a required field is missing or
    malformed. Optional fields (website, outlet details) never cause rejection.
    """
    abn = (row.get(COLUMN_ABN) or "").strip()
    if not (abn.isdigit() and len(abn) == 11):
        raise RegisterRowError("invalid_abn")
    business_name = (row.get(COLUMN_BUSINESS_NAME) or "").strip()
    if not business_name:
        raise RegisterRowError("missing_business_name")
    status = (row.get(COLUMN_STATUS) or "").strip()
    if status not in REGISTRATION_STATUSES:
        raise RegisterRowError("invalid_status")
    try:
        expires_on = parse_register_date(row.get(COLUMN_EXPIRY) or "")
    except ValueError as exc:
        raise RegisterRowError("invalid_expiry_date") from exc

    head_office = parse_address_tail(row.get(COLUMN_HEAD_OFFICE) or "")
    outlet_address_raw = _blank_to_none(row.get(COLUMN_OUTLET_ADDRESS))
    outlet_address = parse_address_tail(outlet_address_raw or "")
    outlet_in_brisbane = is_brisbane_postcode(outlet_address.postcode)
    website = _blank_to_none(row.get(COLUMN_WEBSITE))

    provider = Provider(
        abn=abn,
        business_name=business_name,
        legal_name=(row.get(COLUMN_LEGAL_NAME) or "").strip() or business_name,
        head_office=head_office,
        website=website,
        website_domain=website_domain(website),
        registration_status=status,
        registration_expires_on=expires_on,
        registration_groups=split_registration_groups(row.get(COLUMN_GROUPS) or ""),
        brisbane_area=is_brisbane_postcode(head_office.postcode) or outlet_in_brisbane,
    )
    outlet = Outlet(
        provider_abn=abn,
        name=_blank_to_none(row.get(COLUMN_OUTLET_NAME)),
        address_raw=outlet_address_raw,
        address=outlet_address,
        phone=_blank_to_none(row.get(COLUMN_OUTLET_PHONE)),
        brisbane_area=outlet_in_brisbane,
    )
    return provider, outlet
