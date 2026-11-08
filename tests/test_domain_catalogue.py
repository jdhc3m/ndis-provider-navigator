"""Pure catalogue parsing rules and ``price_limit`` against hand-checked workbook cells."""

from datetime import date
from decimal import Decimal

import pytest

from ndis.domain.catalogue import (
    CATALOGUE_COLUMNS,
    CatalogueRowError,
    CellValue,
    parse_catalogue_date,
    parse_catalogue_row,
    parse_money,
    price_limit,
)
from ndis.domain.entities import PriceLimits, Remoteness, SupportItem
from tests.conftest import CURRENT_ROWS, LEGACY_ROWS, catalogue_row

CURRENT = {row["Support Item Number"]: row for row in CURRENT_ROWS}
LEGACY = {row["Support Item Number"]: row for row in LEGACY_ROWS}


def item(number: str, *, legacy: bool = False) -> SupportItem:
    return parse_catalogue_row((LEGACY if legacy else CURRENT)[number], legacy=legacy)


# Ten price values hand-checked in ``NDIS-Support-Catalogue-2026-27 v1_1.xlsx`` by opening the
# workbook with openpyxl and reading the stored cell text in the sheet XML. Each case records
# (sheet, row, column header / cell, raw stored value). The 2026-27 catalogue has no per-state
# price columns, so the same cell is the limit for every state; the distinctions tested here
# are between the National, Remote and Very Remote tiers.
HAND_CHECKED = [
    # 1. "Current Support Items" row 2, "National" (M2) = 82.57
    ("01_002_0107_1_1", False, "QLD", Remoteness.NATIONAL, "82.57"),
    # 2. "Current Support Items" row 2, "Remote" (N2) = 115.6
    ("01_002_0107_1_1", False, "NT", Remoteness.REMOTE, "115.60"),
    # 3. "Current Support Items" row 2, "Very Remote" (O2) = 123.86
    ("01_002_0107_1_1", False, "WA", Remoteness.VERY_REMOTE, "123.86"),
    # 4. "Current Support Items" row 567, "National" (M567) = 100.14
    ("07_002_0106_8_3", False, "NSW", Remoteness.NATIONAL, "100.14"),
    # 5. "Current Support Items" row 696, "Remote" (N696) = 271.58999999999997 (Excel's
    #    serialisation of the double for 271.59; the sheet displays 271.59)
    ("15_005_0118_1_3", False, "TAS", Remoteness.REMOTE, "271.59"),
    # 6. "Current Support Items" row 756, "Very Remote" (O756) = 234.24
    ("15_056_0128_1_3", False, "SA", Remoteness.VERY_REMOTE, "234.24"),
    # 7. "Current Support Items" row 232, "National" (M232) = 1 (a "Unit Price = $1" item)
    ("03_040000919_0103_1_1", False, "VIC", Remoteness.NATIONAL, "1.00"),
    # 8. "Current Support Items" row 98, "National" (M98) = 97 (stored as a whole number)
    ("01_650_0118_1_3_PT", False, "ACT", Remoteness.NATIONAL, "97.00"),
    # 9. "Legacy Support Items" row 27, "National" (M27) = 708.75
    ("01_045_0115_1_1", True, "QLD", Remoteness.NATIONAL, "708.75"),
    # 10. "Legacy Support Items" row 38, "Very Remote" (O38) = 6405.38
    ("01_061_0115_1_1", True, "NT", Remoteness.VERY_REMOTE, "6405.38"),
]


@pytest.mark.parametrize(("number", "legacy", "state", "remoteness", "expected"), HAND_CHECKED)
def test_price_limit_matches_hand_checked_cells(
    number: str, legacy: bool, state: str, remoteness: Remoteness, expected: str
) -> None:
    limit = price_limit(item(number, legacy=legacy), state, remoteness)
    assert limit == Decimal(expected)
    assert isinstance(limit, Decimal)
    assert str(limit) == expected


def test_remoteness_tiers_are_distinct_and_states_share_the_national_limit() -> None:
    weekday_night = item("01_002_0107_1_1")
    assert price_limit(weekday_night, "QLD", Remoteness.NATIONAL) == Decimal("82.57")
    assert price_limit(weekday_night, "QLD", Remoteness.REMOTE) == Decimal("115.60")
    assert price_limit(weekday_night, "QLD", Remoteness.VERY_REMOTE) == Decimal("123.86")
    for state in ("ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"):
        assert price_limit(weekday_night, state, Remoteness.NATIONAL) == Decimal("82.57")


def test_price_limit_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="state must be one of"):
        price_limit(item("01_002_0107_1_1"), "NZ", Remoteness.NATIONAL)


def test_quotable_and_unpriced_items_have_no_limit() -> None:
    # "Current Support Items" row 3: Quote = Yes, M3/N3/O3 empty.
    live_in = item("01_003_0107_1_1")
    assert live_in.quotable is True
    assert live_in.limits == PriceLimits(None, None, None)
    assert price_limit(live_in, "QLD", Remoteness.REMOTE) is None
    # "Legacy Support Items" row 2: Quote = Yes, price cells hold empty strings.
    meals = item("01_022_0120_1_1", legacy=True)
    assert meals.quotable is True and meals.legacy is True
    assert meals.end_date == date(2027, 9, 30)
    assert price_limit(meals, "QLD", Remoteness.NATIONAL) is None
    sil = item("01_821_0115_1_1")
    assert (sil.quotable, sil.unit, sil.limits.national) == (False, "WK", None)


def test_parse_catalogue_row_fields() -> None:
    parsed = item("01_002_0107_1_1")
    assert parsed == SupportItem(
        item_number="01_002_0107_1_1",
        name="Assistance With Self-Care Activities - Standard - Weekday Night",
        registration_group_number="0107",
        registration_group_name="Daily Personal Activities",
        unit="H",
        quotable=False,
        start_date=date(2026, 7, 1),
        end_date=None,
        limits=PriceLimits(Decimal("82.57"), Decimal("115.60"), Decimal("123.86")),
        legacy=False,
    )
    legacy = item("01_045_0115_1_1", legacy=True)
    assert (legacy.legacy, legacy.unit, legacy.end_date) == (True, "D", date(2027, 6, 30))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (82.57, "82.57"),
        (115.6, "115.60"),
        (271.58999999999997, "271.59"),
        (1, "1.00"),
        (97, "97.00"),
        (1.0, "1.00"),
        (" 6405.38 ", "6405.38"),
        ("0", "0.00"),
    ],
)
def test_parse_money_yields_exact_cents(value: CellValue, expected: str) -> None:
    assert parse_money(value) == Decimal(expected)
    assert str(parse_money(value)) == expected


@pytest.mark.parametrize("value", [None, "", "   "])
def test_parse_money_blank_means_no_limit(value: CellValue) -> None:
    assert parse_money(value) is None


@pytest.mark.parametrize("value", ["abc", "$82.57", 82.575, -1, "NaN", "Infinity"])
def test_parse_money_rejects_non_amounts(value: CellValue) -> None:
    with pytest.raises(CatalogueRowError, match="invalid_price"):
        parse_money(value)


def test_parse_catalogue_date() -> None:
    assert parse_catalogue_date(20260701) == date(2026, 7, 1)
    assert parse_catalogue_date("20270630") == date(2027, 6, 30)
    assert parse_catalogue_date(20260703.0) == date(2026, 7, 3)
    assert parse_catalogue_date(99991231) is None
    with pytest.raises(CatalogueRowError, match="invalid_date"):
        parse_catalogue_date("2026-07-01")
    with pytest.raises(CatalogueRowError, match="invalid_date"):
        parse_catalogue_date(None)


def good_row(**overrides: CellValue) -> dict[str, CellValue]:
    row = catalogue_row(
        "01_011_0107_1_1", "Weekday Daytime", "0107", "Daily Personal Activities", "H", "No",
        20260701, 99991231, 73.58, 103.01, 110.37,
    )  # fmt: skip
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"Support Item Number": None}, "missing_item_number"),
        ({"Support Item Number": "  "}, "missing_item_number"),
        ({"Support Item Name": ""}, "missing_item_name"),
        ({"Registration Group Number": None}, "missing_registration_group"),
        ({"Registration Group Name": ""}, "missing_registration_group"),
        ({"Unit": None}, "missing_unit"),
        ({"Quote": "Maybe"}, "invalid_quote_flag"),
        ({"Quote": None}, "invalid_quote_flag"),
        ({"Start date": 20250701}, "start_date_outside_financial_year"),
        ({"Start date": 20270701}, "start_date_outside_financial_year"),
        ({"Start date": 99991231}, "start_date_outside_financial_year"),
        ({"Start date": "July 2026"}, "invalid_date"),
        ({"End Date": "never"}, "invalid_date"),
        ({"Remote": "n/a"}, "invalid_price"),
    ],
)
def test_parse_catalogue_row_rejects_bad_rows(overrides: dict[str, CellValue], reason: str) -> None:
    with pytest.raises(CatalogueRowError) as info:
        parse_catalogue_row(good_row(**overrides), legacy=False)
    assert info.value.reason == reason


def test_financial_year_boundaries_are_inclusive() -> None:
    assert parse_catalogue_row(good_row(**{"Start date": 20260701}), legacy=False).start_date
    assert parse_catalogue_row(good_row(**{"Start date": 20270630}), legacy=False).start_date
    assert parse_catalogue_row(good_row(Quote="YES"), legacy=False).quotable is True


def test_catalogue_columns_are_all_in_the_fixture_header() -> None:
    assert set(CATALOGUE_COLUMNS) <= set(good_row())
