"""Pure register parsing rules on awkward real-format rows."""

import csv
from datetime import date

import pytest

from ndis.domain.entities import AddressParts, Outlet, RegistrationGroup
from ndis.domain.register import (
    RegisterRowError,
    is_brisbane_postcode,
    parse_address_tail,
    parse_register_date,
    parse_register_row,
    split_registration_groups,
    website_domain,
)
from tests.conftest import BAD_ROWS, GOOD_ROWS, REGISTER_HEADER


def rows(lines: list[str]) -> list[dict[str, str]]:
    return list(csv.DictReader([REGISTER_HEADER, *lines]))


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("CARDIFF, NSW, 2285, AU", AddressParts("CARDIFF", "NSW", "2285")),
        ("LEVEL 1, IPSWICH, QLD, 4305, AU", AddressParts("IPSWICH", "QLD", "4305")),
        ("Turn into Curry Street, 39 Camellia ST, Cardiff, NSW, 2285, AU",
         AddressParts("Cardiff", "NSW", "2285")),
        ("AU", AddressParts()),
        ("", AddressParts()),
        ("7 New Line Road, DURAL, NSW, 21578, AU", AddressParts("DURAL", "NSW", None)),
        ("1 Main St, Auckland, NZ, 1010, NZ", AddressParts()),
    ],
)  # fmt: skip
def test_parse_address_tail(address: str, expected: AddressParts) -> None:
    assert parse_address_tail(address) == expected


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("https://www.SmithJonesCare.com.au/about/us?x=1", "smithjonescare.com.au"),
        ("http://huntercarers.org.au", "huntercarers.org.au"),
        ("www.example.com.au:8080/path", "example.com.au"),
        ("example.com", "example.com"),
        ("HTTPS://WWW.EXAMPLE.COM/", "example.com"),
        ("https://188 George Street", None),
        ("localhost", None),
        ("", None),
        (None, None),
    ],
)
def test_website_domain(website: str | None, expected: str | None) -> None:
    assert website_domain(website) == expected


def test_split_registration_groups_keeps_commas_inside_names() -> None:
    groups = split_registration_groups(
        "Assist-Personal Activities; Assist-Life Stage, Transition; ; Assist-Personal Activities"
    )
    assert groups == (
        RegistrationGroup("Assist-Personal Activities"),
        RegistrationGroup("Assist-Life Stage, Transition"),
    )
    assert split_registration_groups("") == ()


def test_parse_register_date() -> None:
    assert parse_register_date("10 November 2028") == date(2028, 11, 10)
    with pytest.raises(ValueError):
        parse_register_date("someday")


@pytest.mark.parametrize(
    ("postcode", "expected"),
    [("4000", True), ("4101", True), ("4207", True), ("4208", False), ("4299", False),
     ("4300", True), ("4305", True), ("4306", False), ("2285", False), (None, False),
     ("abcd", False)],
)  # fmt: skip
def test_is_brisbane_postcode(postcode: str | None, expected: bool) -> None:
    assert is_brisbane_postcode(postcode) is expected


def test_row_with_comma_group_and_full_outlet() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[0])
    assert provider.abn == "48055975927"
    assert provider.head_office == AddressParts("CARDIFF", "NSW", "2285")
    assert provider.website_domain == "huntercarers.org.au"
    assert provider.registration_expires_on == date(2028, 11, 10)
    assert [group.name for group in provider.registration_groups] == [
        "Assist-Personal Activities",
        "Assist-Life Stage, Transition",
        "Daily Tasks/Shared Living",
    ]
    assert provider.brisbane_area is False
    assert outlet.name == "Hunter Carers Ltd"
    assert outlet.address == AddressParts("Cardiff", "NSW", "2285")
    assert outlet.phone == "+610249540040"
    assert outlet.brisbane_area is False


def test_row_with_comma_and_quotes_in_name_and_www_path_website() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[1])
    assert provider.business_name == 'Smith, Jones "Care" Pty Ltd'
    assert provider.website == "https://www.SmithJonesCare.com.au/about/us?x=1"
    assert provider.website_domain == "smithjonescare.com.au"
    assert provider.head_office.postcode == "4101"
    assert provider.brisbane_area is True
    assert outlet.brisbane_area is True
    assert outlet.address == AddressParts("West End", "QLD", "4101")


def test_revoked_row_with_bare_au_head_office_and_empty_outlet() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[2])
    assert provider.registration_status == "Revoked"
    assert provider.head_office == AddressParts()
    assert provider.website == "https://188 George Street"
    assert provider.website_domain is None
    assert provider.registration_groups == ()
    assert provider.brisbane_area is False
    assert outlet == Outlet(
        provider_abn="74674814036",
        name=None,
        address_raw=None,
        address=AddressParts(),
        phone=None,
        brisbane_area=False,
    )


def test_row_with_bare_au_outlet_address() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[3])
    assert provider.head_office == AddressParts("IPSWICH", "QLD", "4305")
    assert provider.brisbane_area is True
    assert outlet.address_raw == "AU"
    assert outlet.address == AddressParts()
    assert outlet.phone is None
    assert outlet.brisbane_area is False


def test_row_with_empty_outlet_address_and_no_website() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[4])
    assert provider.website is None
    assert provider.website_domain is None
    assert provider.registration_groups == ()
    assert provider.brisbane_area is False
    assert outlet.address_raw is None
    assert outlet.address == AddressParts()


def test_brisbane_outlet_flags_row_even_with_interstate_head_office() -> None:
    provider, outlet = parse_register_row(rows(GOOD_ROWS)[5])
    assert provider.head_office.state == "NSW"
    assert outlet.address == AddressParts("FORTITUDE VALLEY", "QLD", "4006")
    assert outlet.brisbane_area is True
    assert provider.brisbane_area is True
    assert outlet.phone == "136186"


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (BAD_ROWS[0], "invalid_abn"),
        (BAD_ROWS[1], "invalid_expiry_date"),
        (GOOD_ROWS[0].replace("Approved", "Pending"), "invalid_status"),
        (GOOD_ROWS[0].replace('"HUNTER CARERS LTD","HUNTER CARERS LTD"', '"","HUNTER"'),
         "missing_business_name"),
    ],
)  # fmt: skip
def test_rejected_rows_carry_a_reason(line: str, reason: str) -> None:
    with pytest.raises(RegisterRowError) as excinfo:
        parse_register_row(rows([line])[0])
    assert excinfo.value.reason == reason


def test_legal_name_falls_back_to_business_name() -> None:
    line = GOOD_ROWS[4].replace('"PARRAMATTA PLAN MANAGERS PTY LTD"', '""')
    provider, _ = parse_register_row(rows([line])[0])
    assert provider.legal_name == "Parramatta Plan Managers"
