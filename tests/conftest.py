"""Shared fixtures: inline register rows in the real CSV format, catalogue rows copied from the
real workbook (written to a small XLSX with the same sheet/column layout), seeded SQLite repos,
a synthetic Maps export with crawled pages, and a local HTTP site for the fetcher."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import openpyxl
import pytest

from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.domain.catalogue import CellValue
from ndis.domain.entities import (
    AddressParts,
    Outlet,
    PriceLimits,
    Provider,
    RegistrationGroup,
    SupportItem,
)
from ndis.domain.entity_resolution import MapsPlace
from ndis.domain.register import website_domain

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


# --- Support catalogue -----------------------------------------------------------------

# The 21-column header of both sheets of ``NDIS-Support-Catalogue-2026-27 v1_1.xlsx``.
CATALOGUE_HEADER = (
    "Support Item Number", "Support Item Name", "Registration Group Number",
    "Registration Group Name", "Support Category Number", "Support Category Number (PACE)",
    "Support Category Name", "Support Category Name (PACE)", "Unit", "Quote", "Start date",
    "End Date", "National", "Remote", "Very Remote", "Non-Face-to-Face Support Provision",
    "Provider Travel", "Short Notice Cancellations.", "NDIA Requested Reports",
    "Irregular SIL Supports", "Type",
)  # fmt: skip

type CatalogueRow = dict[str, CellValue]


def catalogue_row(
    item_number: str,
    name: str,
    group_number: str,
    group_name: str,
    unit: str,
    quote: str,
    start: CellValue,
    end: CellValue,
    national: CellValue,
    remote: CellValue,
    very_remote: CellValue,
    kind: str | None = "Priced Supports",
) -> CatalogueRow:
    """A row in the workbook's column layout; the claim-flag columns are filled as ``N``."""
    return {
        "Support Item Number": item_number,
        "Support Item Name": name,
        "Registration Group Number": group_number,
        "Registration Group Name": group_name,
        "Support Category Number": 1,
        "Support Category Number (PACE)": 1,
        "Support Category Name": "Assistance with Daily Life (Includes SIL)",
        "Support Category Name (PACE)": "Assistance with Daily Life",
        "Unit": unit,
        "Quote": quote,
        "Start date": start,
        "End Date": end,
        "National": national,
        "Remote": remote,
        "Very Remote": very_remote,
        "Non-Face-to-Face Support Provision": "N",
        "Provider Travel": "N",
        "Short Notice Cancellations.": "N",
        "NDIA Requested Reports": "N",
        "Irregular SIL Supports": "N",
        "Type": kind,
    }


# Rows copied cell for cell from the real workbook (sheet, row). Prices are the Python values
# openpyxl yields for those cells: floats for decimal amounts, ints for whole dollars.
CURRENT_ROWS: list[CatalogueRow] = [
    # Current Support Items row 2
    catalogue_row("01_002_0107_1_1",
                  "Assistance With Self-Care Activities - Standard - Weekday Night",
                  "0107", "Daily Personal Activities", "H", "No", 20260701, 99991231,
                  82.57, 115.6, 123.86),
    # Current Support Items row 3: quotable, no prices (empty cells read as None)
    catalogue_row("01_003_0107_1_1", "Assistance From Live-In Carer", "0107",
                  "Daily Personal Activities", "H", "Yes", 20260701, 99991231,
                  None, None, None, kind="Quotable Supports"),
    # Current Support Items row 98: whole-dollar national price stored as the integer 97
    catalogue_row("01_650_0118_1_3_PT",
                  "ECI Professional - Occupational Therapist - Provider Travel",
                  "0118", "Early Intervention Supports For Early Childhood", "H", "No",
                  20260701, 99991231, 97, 135.8, 145.5),
    # Current Support Items row 232: "Unit Price = $1" item
    catalogue_row("03_040000919_0103_1_1", "Disability-Related Health Consumables - Low Cost",
                  "0103", "Assistive Products For Personal Care And Safety", "E", "No",
                  20260701, 99991231, 1, 1, 1, kind="Unit Price = $1"),
    # Current Support Items row 567
    catalogue_row("07_002_0106_8_3", "Support Coordination Level 2: Coordination of Supports",
                  "0106",
                  "Assistance In Coordinating Or Managing Life Stages, Transitions And Supports",
                  "H", "No", 20260701, 99991231, 100.14, 140.19, 150.21),
    # Current Support Items row 696 (N696 is stored in the XML as 271.58999999999997)
    catalogue_row("15_005_0118_1_3",
                  "ECI Professional - Other Early Childhood Professional - Direct Service",
                  "0118", "Early Intervention Supports For Early Childhood", "H", "No",
                  20260701, 99991231, 193.99, 271.58999999999997, 290.99),
    # Current Support Items row 756
    catalogue_row("15_056_0128_1_3", "Therapy - Other Professional - Direct Service", "0128",
                  "Therapeutic Supports", "H", "No", 20260701, 99991231, 156.16, 218.62, 234.24),
    # Current Support Items SIL item: not priced and not quotable, Type "Priced Supports"
    catalogue_row("01_821_0115_1_1", "Assistance in Supported Independent Living - Weekly", "0115",
                  "Assistance With Daily Life Tasks In A Group Or Shared Living Arrangement",
                  "WK", "No", 20260701, 99991231, None, None, None, kind="Priced Supports"),
]  # fmt: skip

LEGACY_ROWS: list[CatalogueRow] = [
    # Legacy Support Items row 2: quotable with empty-string price cells and an end date
    catalogue_row("01_022_0120_1_1",
                  "Assistance With The Cost Of Preparation And Delivery Of Meals",
                  "0120", "Household Tasks", "E", "Yes", 20260701, 20270930, "", "", "",
                  kind="Quotable Supports"),
    # Legacy Support Items row 27
    catalogue_row("01_045_0115_1_1", "STA And Assistance (Inc. Respite) - 1:4 - Weekday", "0115",
                  "Assistance With Daily Life Tasks In A Group Or Shared Living Arrangement",
                  "D", "No", 20260701, 20270630, 708.75, 992.25, 1063.13),
    # Legacy Support Items row 38
    catalogue_row("01_061_0115_1_1", "STA And Assistance (Inc. Respite) - 1:1 - Public Holiday",
                  "0115",
                  "Assistance With Daily Life Tasks In A Group Or Shared Living Arrangement",
                  "D", "No", 20260701, 20270630, 4270.25, 5978.35, 6405.38),
]  # fmt: skip

BAD_CATALOGUE_ROWS: list[CatalogueRow] = [
    # Start date from the 2025-26 catalogue.
    catalogue_row("01_011_0107_1_1", "Old Year Item", "0107", "Daily Personal Activities", "H",
                  "No", 20250701, 99991231, 70.23, 98.32, 105.35),
    # Sub-cent price.
    catalogue_row("99_001_0107_1_1", "Fractional Cents", "0107", "Daily Personal Activities",
                  "H", "No", 20260701, 99991231, 82.575, 115.6, 123.86),
]  # fmt: skip

# The same number as Current row 2 listed again on the legacy sheet with another price.
DUPLICATE_LEGACY_ROW = catalogue_row(
    "01_002_0107_1_1", "Older listing of the same item", "0107", "Daily Personal Activities",
    "H", "No", 20260701, 20260930, 1.0, 2.0, 3.0,
)  # fmt: skip


def write_catalogue(
    path: Path,
    current: list[CatalogueRow],
    legacy: list[CatalogueRow],
    *,
    header: tuple[str, ...] = CATALOGUE_HEADER,
    sheets: tuple[str, str] = ("Current Support Items", "Legacy Support Items"),
) -> Path:
    """Write a workbook with the real catalogue's two sheets and header row."""
    workbook = openpyxl.Workbook()
    first = workbook.active
    assert first is not None
    first.title = sheets[0]
    second = workbook.create_sheet(sheets[1])
    for sheet, rows in ((first, current), (second, legacy)):
        sheet.append(list(header))
        for row in rows:
            sheet.append([row.get(column) for column in header])
    workbook.save(path)
    return path


@pytest.fixture
def catalogue_xlsx(tmp_path: Path) -> Path:
    return write_catalogue(tmp_path / "support_catalogue.xlsx", CURRENT_ROWS, LEGACY_ROWS)


@pytest.fixture
def catalogue_xlsx_with_bad_rows(tmp_path: Path) -> Path:
    return write_catalogue(
        tmp_path / "support_catalogue_bad.xlsx",
        CURRENT_ROWS + BAD_CATALOGUE_ROWS,
        LEGACY_ROWS + [DUPLICATE_LEGACY_ROW],
    )


def make_support_item(
    item_number: str,
    name: str,
    *,
    national: str | None = "82.57",
    remote: str | None = "115.60",
    very_remote: str | None = "123.86",
    quotable: bool = False,
    legacy: bool = False,
    end_date: date | None = None,
) -> SupportItem:
    return SupportItem(
        item_number=item_number,
        name=name,
        registration_group_number="0107",
        registration_group_name="Daily Personal Activities",
        unit="H",
        quotable=quotable,
        start_date=date(2026, 7, 1),
        end_date=end_date,
        limits=PriceLimits(
            national=None if national is None else Decimal(national),
            remote=None if remote is None else Decimal(remote),
            very_remote=None if very_remote is None else Decimal(very_remote),
        ),
        legacy=legacy,
    )


SEED_SUPPORT_ITEMS = [
    make_support_item("01_002_0107_1_1", "Assistance With Self-Care Activities - Weekday Night"),
    make_support_item(
        "01_003_0107_1_1",
        "Assistance From Live-In Carer",
        national=None,
        remote=None,
        very_remote=None,
        quotable=True,
    ),
    make_support_item(
        "01_045_0115_1_1",
        "STA And Assistance (Inc. Respite) - 1:4 - Weekday",
        national="708.75",
        remote="992.25",
        very_remote="1063.13",
        legacy=True,
        end_date=date(2027, 6, 30),
    ),
]


@pytest.fixture
def seeded_support_items(db_path: Path) -> SqlAlchemySupportItemRepository:
    repository = SqlAlchemySupportItemRepository(create_sqlite_engine(db_path))
    repository.replace_all(SEED_SUPPORT_ITEMS)
    return repository


# --- Maps export (synthetic; the real one is private and never leaves data/private/) --------

# The ``providers``, ``reviews`` and ``pages`` tables of the scraper's SQLite file, copied
# from db/schema.sql.
MAPS_SCHEMA = """
CREATE TABLE providers (
    place_id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT, categories TEXT,
    address TEXT, suburb TEXT, state TEXT, postcode TEXT, phone TEXT, website TEXT,
    emails TEXT, review_rating REAL, review_count INTEGER, reviews_per_rating TEXT,
    latitude REAL, longitude REAL, maps_link TEXT, open_hours TEXT, instagram TEXT,
    facebook TEXT, linkedin TEXT, tiktok TEXT, youtube TEXT, x TEXT, scraped_at TEXT NOT NULL
);
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY, place_id TEXT NOT NULL REFERENCES providers(place_id),
    rating INTEGER, when_text TEXT, text TEXT
);
CREATE TABLE pages (
    id INTEGER PRIMARY KEY, place_id TEXT NOT NULL REFERENCES providers(place_id),
    url TEXT NOT NULL, status TEXT NOT NULL, title TEXT, text TEXT, fetched_at TEXT NOT NULL,
    UNIQUE (place_id, url)
);
"""

# (place_id, title, category, website, suburb, postcode, latitude, longitude); shapes seen in
# the real export: utm-decorated URLs, blank website, blank category, decorated titles.
MAPS_ROWS = [
    ("place-alpha", "Alpha Therapy - NDIS Provider Brisbane", "Physiotherapist",
     "https://www.10000000001.example.com.au/about?utm_source=gbp", "West End", "4101",
     -27.48, 153.01),
    ("place-beta", "Beta Supports", "Disability services and support organization",
     None, "Brisbane City", "4000", -27.47, 153.02),
    ("place-delta", "Delta Revoked", "", "http://10000000004.example.com.au", "Milton",
     "4064", None, None),
    ("place-recruit", "Hire Co", "Recruitment Agency", "https://hireco.example.com",
     "Milton", "4064", -27.46, 153.0),
    ("place-unknown", "Nobody Known Pty Ltd", "Disability services and support organization",
     "https://nobody.example.com", " ", "", -27.5, 153.03),
]  # fmt: skip


# (place_id, url, status, title, text, fetched_at); shapes seen in the real crawl: several
# ``ok`` pages per place, an ``ok`` page with blank text, and every failure status.
MAPS_PAGES = [
    ("place-alpha", "https://www.10000000001.example.com.au/", "ok", "Alpha Therapy",
     "Alpha Therapy\nPhysiotherapy and occupational therapy for NDIS participants.\n\n\n",
     "2026-11-07T03:49:42+00:00"),
    ("place-alpha", "https://www.10000000001.example.com.au/about", "ok", None,
     "About Alpha Therapy.   \nWe are a team of allied health professionals in West End.",
     "2026-11-07T03:49:43+00:00"),
    ("place-alpha", "https://www.10000000001.example.com.au/missing", "http_404", None, None,
     "2026-11-07T03:49:44+00:00"),
    ("place-beta", "https://beta.example/", "ok", "", "  \n ", "2026-11-07T03:50:00+00:00"),
    ("place-delta", "http://10000000004.example.com.au/", "timeout", None, None,
     "2026-11-07T03:51:00+00:00"),
    ("place-recruit", "https://hireco.example.com/", "ok", "Hire Co",
     "Recruitment for the care sector.", "2026-11-07T03:52:00+00:00"),
    ("place-unknown", "https://nobody.example.com/", "error:SSL", None, None,
     "2026-11-07T03:53:00+00:00"),
]  # fmt: skip


def write_maps_export(
    path: Path, rows: list[tuple[object, ...]], pages: list[tuple[object, ...]] | None = None
) -> Path:
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(MAPS_SCHEMA)
        connection.executemany(
            "insert into providers (place_id, title, category, website, suburb, postcode, "
            "latitude, longitude, scraped_at) values (?, ?, ?, ?, ?, ?, ?, ?, '2026-11-01')",
            rows,
        )
        connection.execute(
            "insert into reviews (place_id, rating, when_text, text) "
            "values ('place-alpha', 5, 'a week ago', 'never read by the matcher')"
        )
        connection.executemany(
            "insert into pages (place_id, url, status, title, text, fetched_at) "
            "values (?, ?, ?, ?, ?, ?)",
            pages or [],
        )
    connection.close()
    return path


@pytest.fixture
def maps_export(tmp_path: Path) -> Path:
    return write_maps_export(tmp_path / "maps_brisbane.db", MAPS_ROWS, MAPS_PAGES)


def make_place(
    place_id: str,
    title: str,
    *,
    category: str | None = "Disability services and support organization",
    website: str | None = None,
    postcode: str | None = "4101",
) -> MapsPlace:
    return MapsPlace(
        place_id=place_id,
        title=title,
        category=category,
        website=website,
        website_domain=website_domain(website),
        suburb="WEST END",
        postcode=postcode,
        latitude=-27.48,
        longitude=153.01,
    )


# --- Local HTTP fixture for the polite fetcher ---------------------------------------------


class LocalSite:
    """A real HTTP server on localhost that records every request it receives.

    ``robots`` is served at ``/robots.txt`` with ``robots_status`` after ``robots_delay``
    seconds (``None`` → 404; ``robots_redirect_to`` set → 302 there instead). Paths:
    ``/page`` and ``/other`` return small HTML documents, ``/slow`` sleeps for
    ``slow_seconds`` before answering, ``/500`` answers 500, ``/404`` answers 404, ``/big``
    streams ``big_bytes`` of body, ``/pdf`` answers with a PDF media type, ``/drop`` closes
    the connection without answering, ``/redirect`` answers 302 to ``redirect_to`` (``None``
    → 302 without ``Location``), ``/loop`` redirects to itself and ``/private/secret`` is
    what a ``Disallow: /private`` rule should keep us away from.
    """

    def __init__(self) -> None:
        self.robots: str | None = ""
        self.robots_status = 200
        self.robots_delay = 0.0
        self.slow_seconds = 2.0
        self.big_bytes = 4096
        self.redirect_to: str | None = "/page"
        self.robots_redirect_to: str | None = None
        self.requests: list[tuple[str, float, str | None]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def do_GET(self) -> None:
                site.requests.append((self.path, time.monotonic(), self.headers.get("User-Agent")))
                try:
                    self._answer()
                except (BrokenPipeError, ConnectionResetError):
                    return

            def _answer(self) -> None:
                if self.path == "/robots.txt":
                    time.sleep(site.robots_delay)
                    if site.robots_redirect_to is not None:
                        return self._redirect(site.robots_redirect_to)
                    if site.robots is None:
                        return self._send(404, b"nothing here")
                    body = site.robots.encode("utf-8")
                    return self._send(site.robots_status, body, "text/plain")
                if self.path == "/slow":
                    time.sleep(site.slow_seconds)
                    return self._send(200, b"<html><body>late</body></html>")
                if self.path == "/500":
                    return self._send(500, b"server error")
                if self.path == "/404":
                    return self._send(404, b"not here")
                if self.path == "/big":
                    return self._send(200, b"<html><body>" + b"x" * site.big_bytes)
                if self.path == "/pdf":
                    return self._send(200, b"%PDF-1.4 not html", "application/pdf")
                if self.path == "/drop":
                    self.close_connection = True
                    return self.connection.close()
                if self.path in {"/redirect", "/loop"}:
                    return self._redirect("/loop" if self.path == "/loop" else site.redirect_to)
                name = self.path.strip("/") or "page"
                body = PAGE_HTML.replace(b"{name}", name.encode("utf-8"))
                return self._send(200, body)

            def _redirect(self, target: str | None) -> None:
                self.send_response(302)
                if target is not None:
                    self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send(self, status: int, body: bytes, content_type: str = "text/html") -> None:
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}{path}"

    def paths(self) -> list[str]:
        return [path for path, _, _ in self.requests]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


PAGE_HTML = b"""<!doctype html><html><head><title>Acme Care | {name}</title></head><body>
<nav><ul><li><a href="/">Home</a></li><li><a href="/about">About us</a></li>
<li><a href="/contact">Contact</a></li></ul></nav>
<main><h1>Acme Care {name}</h1>
<p>Acme Care provides support coordination and daily personal activities for NDIS
participants across Brisbane. Our team of experienced support workers helps you live the
life you choose, at home and in the community, seven days a week.</p></main>
<footer><p>Copyright 2026 Acme Care. Privacy policy. Terms of use.</p></footer>
</body></html>"""


@pytest.fixture
def local_site() -> Iterator[LocalSite]:
    site = LocalSite()
    yield site
    site.close()
