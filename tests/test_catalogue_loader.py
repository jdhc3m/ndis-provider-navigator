"""Catalogue XLSX reader and loader: both sheets, counts, duplicates and the parse-ratio gate."""

import logging
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import pytest

from ndis.adapters.catalogue_xlsx import (
    CatalogueFormatError,
    count_distinct_items,
    iter_catalogue_rows,
    load_catalogue,
    parse_catalogue,
)
from ndis.adapters.sqlite_repository import SqlAlchemySupportItemRepository, create_sqlite_engine
from ndis.domain.entities import SupportItem
from scripts.load_catalogue import main as load_catalogue_main
from tests.conftest import CATALOGUE_HEADER, CURRENT_ROWS, LEGACY_ROWS, write_catalogue


def test_iter_catalogue_rows_reads_both_sheets_in_order(catalogue_xlsx: Path) -> None:
    rows = list(iter_catalogue_rows(catalogue_xlsx))
    assert [sheet for sheet, _ in rows] == ["Current Support Items"] * len(CURRENT_ROWS) + [
        "Legacy Support Items"
    ] * len(LEGACY_ROWS)
    first = rows[0][1]
    assert first["Support Item Number"] == "01_002_0107_1_1"
    assert (first["National"], first["Remote"], first["Start date"]) == (82.57, 115.6, 20260701)
    assert count_distinct_items(catalogue_xlsx) == len(CURRENT_ROWS) + len(LEGACY_ROWS)


def test_iter_catalogue_rows_skips_blank_rows_and_reads_part_files(
    tmp_path: Path, catalogue_xlsx: Path
) -> None:
    import openpyxl

    workbook = openpyxl.load_workbook(catalogue_xlsx)
    workbook["Current Support Items"].append([None] * len(CATALOGUE_HEADER))
    workbook["Legacy Support Items"].append([""] * len(CATALOGUE_HEADER))
    partial = tmp_path / "support_catalogue.part"
    workbook.save(partial)
    assert len(list(iter_catalogue_rows(partial))) == len(CURRENT_ROWS) + len(LEGACY_ROWS)


def test_iter_catalogue_rows_rejects_non_workbook(tmp_path: Path) -> None:
    junk = tmp_path / "junk.xlsx"
    junk.write_text("<html>blocked</html>\n", encoding="utf-8")
    with pytest.raises(CatalogueFormatError, match="not an XLSX workbook"):
        list(iter_catalogue_rows(junk))


def test_iter_catalogue_rows_rejects_missing_sheet_or_columns(tmp_path: Path) -> None:
    wrong_sheets = write_catalogue(
        tmp_path / "sheets.xlsx", CURRENT_ROWS, [], sheets=("Sheet1", "Legacy Support Items")
    )
    with pytest.raises(CatalogueFormatError, match="missing sheets"):
        list(iter_catalogue_rows(wrong_sheets))
    header = tuple(column for column in CATALOGUE_HEADER if column != "Very Remote")
    wrong_header = write_catalogue(tmp_path / "header.xlsx", CURRENT_ROWS, [], header=header)
    with pytest.raises(CatalogueFormatError, match=r"missing columns: \['Very Remote'\]"):
        list(iter_catalogue_rows(wrong_header))


def test_parse_catalogue_reports_counts_and_rejections(
    catalogue_xlsx_with_bad_rows: Path,
) -> None:
    items, report = parse_catalogue(catalogue_xlsx_with_bad_rows)
    assert report.input_rows == len(CURRENT_ROWS) + len(LEGACY_ROWS) + 3
    assert report.parsed_rows == len(CURRENT_ROWS) + len(LEGACY_ROWS)
    assert report.rejected_rows == 3
    assert dict(report.rejection_reasons) == {
        "start_date_outside_financial_year": 1,
        "invalid_price": 1,
        "duplicate_item_number": 1,
    }
    assert dict(report.rows_per_sheet) == {
        "Current Support Items": len(CURRENT_ROWS) + 2,
        "Legacy Support Items": len(LEGACY_ROWS) + 1,
    }
    assert (report.quotable_items, report.unpriced_items) == (2, 1)
    # The current-sheet row wins over the legacy duplicate.
    assert items["01_002_0107_1_1"].name.startswith("Assistance With Self-Care")
    assert items["01_002_0107_1_1"].limits.national == Decimal("82.57")
    assert "01_011_0107_1_1" not in items
    assert "duplicate_item_number=1" in report.summary()
    assert "Legacy Support Items=4" in report.summary()


def test_load_catalogue_refuses_low_parse_ratio(tmp_path: Path, db_path: Path) -> None:
    old_year = [dict(row, **{"Start date": 20250701}) for row in CURRENT_ROWS]
    path = write_catalogue(tmp_path / "old.xlsx", old_year, LEGACY_ROWS)
    repository = SqlAlchemySupportItemRepository(create_sqlite_engine(db_path))
    with pytest.raises(CatalogueFormatError, match="start_date_outside_financial_year"):
        load_catalogue(path, repository)
    assert repository.get("01_045_0115_1_1") is None


def test_load_catalogue_persists_and_logs(
    catalogue_xlsx: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repository = SqlAlchemySupportItemRepository(create_sqlite_engine(db_path))
    with caplog.at_level(logging.INFO, logger="ndis.adapters.catalogue_xlsx"):
        report = load_catalogue(catalogue_xlsx, repository)
    assert report.items_persisted == len(CURRENT_ROWS) + len(LEGACY_ROWS)
    assert report.parse_ratio == 1.0
    assert f"items_persisted={report.items_persisted}" in caplog.text
    stored = repository.get("15_005_0118_1_3")
    assert stored is not None
    assert stored.limits.remote == Decimal("271.59")
    legacy = repository.get("01_061_0115_1_1")
    assert legacy is not None and legacy.legacy is True
    assert legacy.limits.very_remote == Decimal("6405.38")
    meals = repository.get("01_022_0120_1_1")
    assert meals is not None and meals.quotable and meals.limits.national is None


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: int | None = None

    def replace_all(self, items: Iterable[SupportItem]) -> None:
        self.saved = len(list(items))

    def get(self, item_number: str) -> SupportItem | None:
        return None


def test_loader_goes_through_the_repository_port(catalogue_xlsx: Path) -> None:
    repository = RecordingRepository()
    load_catalogue(catalogue_xlsx, repository)
    assert repository.saved == len(CURRENT_ROWS) + len(LEGACY_ROWS)


def test_load_catalogue_cli(
    catalogue_xlsx: Path, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert load_catalogue_main(["--xlsx", str(catalogue_xlsx), "--db", str(db_path)]) == 0
    assert f"items_persisted={len(CURRENT_ROWS) + len(LEGACY_ROWS)}" in caplog.text
    missing = tmp_path / "missing.xlsx"
    assert load_catalogue_main(["--xlsx", str(missing), "--db", str(db_path)]) == 1
    assert "not found" in caplog.text
    junk = tmp_path / "junk.xlsx"
    junk.write_text("<html>blocked</html>\n", encoding="utf-8")
    assert load_catalogue_main(["--xlsx", str(junk), "--db", str(db_path)]) == 1
    assert "catalogue load failed" in caplog.text
