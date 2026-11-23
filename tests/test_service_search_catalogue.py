"""Service search against the real 2026-27 catalogue workbook.

The expected values here are read from the XLSX itself (raw ``openpyxl`` in this test
module, deliberately not the loader under test), so a search bug or a loader bug cannot
cancel out: every expected item number and price comes from the published workbook.
Skips visibly where the workbook is absent (CI); on machines with ``data/`` it enforces
the measured facts. ``NDIS_CATALOGUE_XLSX`` points the tests at the workbook when the
default path does not exist (a worktree checkout, for instance).
"""

import os
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from ndis.adapters.catalogue_xlsx import parse_catalogue
from ndis.application.catalogue import SearchSupportItems
from ndis.domain.entities import SupportItem

ROOT = Path(__file__).resolve().parents[1]
XLSX_ENV = "NDIS_CATALOGUE_XLSX"
DEFAULT_XLSX = ROOT / "data" / "public" / "support_catalogue.xlsx"


@pytest.fixture(scope="module")
def catalogue_path() -> Path:
    path = Path(os.environ.get(XLSX_ENV) or DEFAULT_XLSX)
    if not path.is_file():
        pytest.skip(f"support catalogue workbook not loaded yet: {path} is missing")
    return path


@pytest.fixture(scope="module")
def items(catalogue_path: Path) -> tuple[SupportItem, ...]:
    parsed, _ = parse_catalogue(catalogue_path)
    return tuple(parsed.values())


def _workbook_rows(path: Path) -> list[tuple[str, str, str | float | int | None]]:
    """``(number, name, national)`` for every row of both sheets, read straight from the
    workbook — the expectations below come from here, never from the code under test."""
    rows: list[tuple[str, str, str | float | int | None]] = []
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.sheetnames:
            worksheet = workbook[sheet]
            generator = worksheet.iter_rows(values_only=True)
            header = next(row for row in generator if row and any(cell is not None for cell in row))
            index = {str(name): i for i, name in enumerate(header) if name is not None}
            for row in generator:
                if row is None or not any(cell is not None for cell in row):
                    continue
                number = str(row[index["Support Item Number"]]).strip()
                name = str(row[index["Support Item Name"]]).strip()
                rows.append((number, name, row[index["National"]]))
    finally:
        workbook.close()
    return rows


def _base_numbers_with_name(rows: list[tuple[str, str, object]], needle: str) -> list[str]:
    """The item numbers whose catalogue name contains ``needle``, with no letter suffix."""
    return [
        number
        for number, name, _ in rows
        if needle in name and number.rsplit("_", 1)[-1][:1].isalpha() is False
    ]


def _national_of(rows: list[tuple[str, str, object]], item_number: str) -> Decimal | None:
    """The workbook's national-limit cell for one item number, as an exact Decimal —
    the same conversion the loader applies, applied here to the raw cell."""
    value = next(national for number, _, national in rows if number == item_number)
    return None if value in (None, "") else Decimal(str(value))


@pytest.fixture(scope="module")
def rows(catalogue_path: Path) -> list[tuple[str, str, str | float | int | None]]:
    return _workbook_rows(catalogue_path)


@pytest.fixture(scope="module")
def search(items: tuple[SupportItem, ...]) -> SearchSupportItems:
    class _Repository:
        def all_items(self) -> tuple[SupportItem, ...]:
            return items

    return SearchSupportItems(_Repository())  # type: ignore[arg-type]


def test_occupational_therapy_and_ot_return_the_five_base_items_first(
    search: SearchSupportItems, rows: list[tuple[str, str, object]]
) -> None:
    """Measured 2026-11-18 against the loaded 2026-27 catalogue: "Occupational
    Therapist" appears in 30 item names; five are base items, each $193.99 per hour
    national, and they come first."""
    expected_numbers = sorted(_base_numbers_with_name(rows, "Occupational Therapist"))
    assert len(expected_numbers) == 5
    for phrase in ("occupational therapy", "OT"):
        result = search(phrase)
        assert result.total == 30
        first_five = result.items[:5]
        assert [item.item_number for item in first_five] == expected_numbers
        for item in first_five:
            assert item.limits.national == _national_of(rows, item.item_number)
            assert item.limits.national is not None
            assert item.unit == "H"
        assert all(item.limits.national == Decimal("193.99") for item in first_five)


def test_speech_therapy_finds_the_therapy_speech_pathologist_items(
    search: SearchSupportItems, rows: list[tuple[str, str, object]]
) -> None:
    result = search("speech therapy")
    numbers = [item.item_number for item in result.items]
    for number in ("01_665_0128_1_3", "15_622_0128_1_3"):
        assert number in numbers
    for number in ("01_665_0128_1_3", "15_622_0128_1_3"):
        item = next(item for item in result.items if item.item_number == number)
        assert item.name == "Therapy - Speech Pathologist - Direct Service"
        assert item.limits.national == _national_of(rows, number) == Decimal("193.99")


def test_support_coordination_returns_the_three_levels(
    search: SearchSupportItems, rows: list[tuple[str, str, object]]
) -> None:
    """The three levels price at $80.06, $100.14 and $190.54 per hour (the Level 2 name
    is shared by two current item numbers, both $100.14)."""
    result = search("support coordination")
    limits = {item.item_number: item.limits.national for item in result.items}
    for number in ("07_001_0106_8_3", "07_002_0106_8_3", "07_004_0132_8_3"):
        assert limits[number] == _national_of(rows, number)
    assert [
        limits[number] for number in ("07_001_0106_8_3", "07_002_0106_8_3", "07_004_0132_8_3")
    ] == [Decimal("80.06"), Decimal("100.14"), Decimal("190.54")]


def test_plan_management_includes_the_monthly_fee(
    search: SearchSupportItems, rows: list[tuple[str, str, object]]
) -> None:
    result = search("plan management")
    item = next(item for item in result.items if item.item_number == "14_034_0127_8_3")
    assert item.name == "Plan Management - Monthly Fee"
    assert item.unit == "MON"
    assert item.limits.national == _national_of(rows, "14_034_0127_8_3") == Decimal("104.45")


def test_the_therapy_abbreviations_reach_the_same_items(
    search: SearchSupportItems,
) -> None:
    """physio -> physiotherapist, psychology -> psychologist: the alias table, checked
    against the real names."""
    physio = search("physio")
    numbers = [item.item_number for item in physio.items]
    assert "01_721_0128_1_3" in numbers and "15_055_0128_1_3" in numbers
    psychology = search("psychology")
    numbers = [item.item_number for item in psychology.items]
    assert "01_701_0128_1_3" in numbers and "15_054_0128_1_3" in numbers


def test_a_phrase_no_catalogue_name_contains_matches_nothing(search: SearchSupportItems) -> None:
    result = search("underwater basket weaving")
    assert result.total == 0 and result.items == ()
