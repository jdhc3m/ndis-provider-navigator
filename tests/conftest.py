"""Shared fixtures: inline register rows in the real CSV format and a seeded SQLite repo."""

from datetime import date
from pathlib import Path

import pytest

from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository, create_sqlite_engine
from ndis.domain.entities import AddressParts, Outlet, Provider, RegistrationGroup

REGISTER_HEADER = (
    '"Provider business name","Legal name",ABN,"Head office address",Website,'
    '"Registration status","Period of registration in force until",'
    '"Approved registration groups","Conditions of registration","Outlet name",'
    '"Outlet address","Outlet phone"'
)

# Each line mirrors a shape seen in the real register (quoting, commas inside names and
# groups, stray ``?`` characters, bare ``AU`` addresses, empty outlets, Revoked rows).
GOOD_ROWS = [
    # Group names containing commas; conditions text with ``?`` and ``;``.
    '"HUNTER CARERS LTD","HUNTER CARERS LTD",48055975927,"CARDIFF, NSW, 2285, AU",'
    'https://huntercarers.org.au,Approved,"10 November 2028",'
    '"Assist-Personal Activities; Assist-Life Stage, Transition; Daily Tasks/Shared Living",'
    '"A condition has been imposed; the provider?s mid-term audit.","Hunter Carers Ltd",'
    '"Turn into Curry Street, 39 Camellia ST, Cardiff, NSW, 2285, AU",+610249540040',
    # Business name with comma and embedded quotes; website with https://www. and a path.
    '"Smith, Jones ""Care"" Pty Ltd","SMITH JONES CARE PTY LTD",11222333444,'
    '"WEST END, QLD, 4101, AU",https://www.SmithJonesCare.com.au/about/us?x=1,Approved,'
    '"02 March 2027","Therapeutic Supports; Early Childhood Supports","",'
    '"Smith Jones West End","12 Boundary St, West End, QLD, 4101, AU",+61733334444',
    # Head office is bare ``AU``; outlet empty; Revoked; website is not a real host.
    '"WE CARE DISABILITY SERVICES (QUIRINDI)Pty Ltd","M.J HALL & T.C HALL & C.M HAM & W WONG",'
    '74674814036,AU,https://188 George Street,Revoked,"18 November 2022","","","","",""',
    # Outlet address is bare ``AU``; head office has an extra leading part before the suburb.
    '"Ipswich Allied Health","IPSWICH ALLIED HEALTH PTY LTD",55666777888,'
    '"LEVEL 1, IPSWICH, QLD, 4305, AU",http://www.ipswichallied.com.au,Approved,'
    '"15 June 2026","Therapeutic Supports","","Ipswich Allied Health",AU,""',
    # Outlet address empty; no website; no groups. Second row for same ABN has a Brisbane
    # outlet so the provider flag must be merged across rows.
    '"Parramatta Plan Managers","PARRAMATTA PLAN MANAGERS PTY LTD",99888777666,'
    '"PARRAMATTA, NSW, 2150, AU",,Approved,"30 September 2029","","","","",""',
    '"Parramatta Plan Managers","PARRAMATTA PLAN MANAGERS PTY LTD",99888777666,'
    '"PARRAMATTA, NSW, 2150, AU",,Approved,"30 September 2029","Plan Management","",'
    '"Brisbane office","Level 3, 100 Brunswick St, FORTITUDE VALLEY, QLD, 4006, AU",136186',
]

# The same ABN with a Revoked row listed before its current Approved registration, as
# happens for a handful of real ABNs; the Approved row must win.
REREGISTERED_ROWS = [
    '"Phoenix Supports","PHOENIX SUPPORTS PTY LTD",22333444555,"TOOWONG, QLD, 4066, AU",'
    'https://phoenixsupports.com.au,Revoked,"01 February 2021","Household Tasks","",'
    '"Phoenix old office","1 Old St, TOOWONG, QLD, 4066, AU",""',
    '"Phoenix Supports Australia","PHOENIX SUPPORTS PTY LTD",22333444555,'
    '"MILTON, QLD, 4064, AU",https://phoenixsupports.com.au,Approved,"01 February 2029",'
    '"Household Tasks; Participate Community","","Phoenix Milton",'
    '"2 New St, MILTON, QLD, 4064, AU",+61733335555',
]

BAD_ROWS = [
    # ABN is not 11 digits.
    '"Bad ABN Co","BAD ABN CO",12345,"CARDIFF, NSW, 2285, AU",,Approved,"10 November 2028",'
    '"","","","",""',
    # Expiry date is not parseable.
    '"Bad Date Co","BAD DATE CO",12312312312,"CARDIFF, NSW, 2285, AU",,Approved,"someday",'
    '"","","","",""',
]


def write_register(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join([REGISTER_HEADER, *rows]) + "\n", encoding="utf-8-sig")
    return path


@pytest.fixture
def register_csv(tmp_path: Path) -> Path:
    return write_register(tmp_path / "register.csv", GOOD_ROWS)


@pytest.fixture
def register_csv_with_bad_rows(tmp_path: Path) -> Path:
    return write_register(tmp_path / "register_bad.csv", GOOD_ROWS + BAD_ROWS)


@pytest.fixture
def register_csv_reregistered(tmp_path: Path) -> Path:
    return write_register(tmp_path / "register_rereg.csv", GOOD_ROWS + REREGISTERED_ROWS)


def make_provider(
    abn: str,
    name: str,
    *,
    state: str | None = "QLD",
    postcode: str | None = "4101",
    status: str = "Approved",
    groups: tuple[str, ...] = ("Therapeutic Supports",),
    brisbane_area: bool = True,
) -> Provider:
    return Provider(
        abn=abn,
        business_name=name,
        legal_name=name.upper(),
        head_office=AddressParts(suburb="SOMEWHERE", state=state, postcode=postcode),
        website=f"https://www.{abn}.example.com.au/",
        website_domain=f"{abn}.example.com.au",
        registration_status=status,
        registration_expires_on=date(2028, 1, 1),
        registration_groups=tuple(RegistrationGroup(group) for group in groups),
        brisbane_area=brisbane_area,
    )


SEED_PROVIDERS = [
    make_provider("10000000001", "Alpha Therapy"),
    make_provider("10000000002", "Beta Supports", postcode="4000", groups=("Plan Management",)),
    make_provider("10000000003", "Gamma Care", state="NSW", postcode="2150", brisbane_area=False),
    make_provider("10000000004", "Delta Revoked", status="Revoked", groups=()),
    make_provider(
        "10000000005",
        "Epsilon National",
        state="VIC",
        postcode="3000",
        groups=("Therapeutic Supports", "Plan Management"),
        brisbane_area=True,
    ),
]

SEED_OUTLETS = [
    Outlet("10000000001", "Alpha West End", "1 Boundary St, WEST END, QLD, 4101, AU",
           AddressParts("WEST END", "QLD", "4101"), "+61700000001", True),
    Outlet("10000000003", "Gamma Sydney", "2 George St, PARRAMATTA, NSW, 2150, AU",
           AddressParts("PARRAMATTA", "NSW", "2150"), None, False),
    # Epsilon's head office is in VIC but it has a Brisbane outlet.
    Outlet("10000000005", "Epsilon Brisbane", "3 Queen St, BRISBANE CITY, QLD, 4000, AU",
           AddressParts("BRISBANE CITY", "QLD", "4000"), None, True),
    Outlet("10000000004", None, None, AddressParts(), None, False),
]  # fmt: skip


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "ndis.db"


@pytest.fixture
def seeded_repository(db_path: Path) -> SqlAlchemyProviderRepository:
    repository = SqlAlchemyProviderRepository(create_sqlite_engine(db_path))
    repository.replace_all(SEED_PROVIDERS, SEED_OUTLETS)
    return repository
