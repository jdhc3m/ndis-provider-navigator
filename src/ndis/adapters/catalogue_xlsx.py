"""Reader and loader for the NDIS Support Catalogue XLSX workbook."""

import logging
import zipfile
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from ndis.application.catalogue import SupportItemRepository
from ndis.domain.catalogue import (
    CATALOGUE_COLUMNS,
    CATALOGUE_SHEETS,
    SHEET_LEGACY,
    CatalogueRowError,
    CellValue,
    parse_catalogue_row,
)
from ndis.domain.entities import SupportItem

logger = logging.getLogger(__name__)

MIN_PARSE_RATIO = 0.95


class CatalogueFormatError(ValueError):
    """The file is not a workbook with the catalogue's sheets and header row."""


def iter_catalogue_rows(path: Path) -> Iterator[tuple[str, Mapping[str, CellValue]]]:
    """Yield ``(sheet name, row)`` for every data row of both catalogue sheets.

    The workbook is opened from a file handle so the extension is not inspected (downloads
    are verified while still named ``.part``). Each row is a mapping from the header text to
    the raw cell value; rows that are entirely blank are skipped.
    """
    with path.open("rb") as handle:
        try:
            workbook = openpyxl.load_workbook(handle, read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise CatalogueFormatError(f"file is not an XLSX workbook: {exc}") from exc
        try:
            missing_sheets = [name for name in CATALOGUE_SHEETS if name not in workbook.sheetnames]
            if missing_sheets:
                raise CatalogueFormatError(f"workbook is missing sheets: {missing_sheets}")
            for sheet_name in CATALOGUE_SHEETS:
                rows = workbook[sheet_name].iter_rows(values_only=True)
                header = tuple(_header_text(cell) for cell in next(rows, ()))
                missing = [column for column in CATALOGUE_COLUMNS if column not in header]
                if missing:
                    raise CatalogueFormatError(
                        f"sheet {sheet_name!r} header is missing columns: {missing}"
                    )
                for values in rows:
                    if all(value is None or value == "" for value in values):
                        continue
                    yield sheet_name, dict(zip(header, values, strict=False))
        finally:
            workbook.close()


def _header_text(cell: object) -> str:
    return str(cell).strip() if cell is not None else ""


def count_distinct_items(path: Path) -> int:
    """Number of distinct item numbers among rows that parse as 2026-27 support items."""
    item_numbers: set[str] = set()
    for sheet_name, row in iter_catalogue_rows(path):
        try:
            item = parse_catalogue_row(row, legacy=sheet_name == SHEET_LEGACY)
        except CatalogueRowError:
            continue
        item_numbers.add(item.item_number)
    return len(item_numbers)


@dataclass
class CatalogueLoadReport:
    """Counts produced by one load of the catalogue."""

    input_rows: int = 0
    parsed_rows: int = 0
    rejected_rows: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)
    rows_per_sheet: Counter[str] = field(default_factory=Counter)
    quotable_items: int = 0
    unpriced_items: int = 0
    items_persisted: int = 0

    @property
    def parse_ratio(self) -> float:
        return self.parsed_rows / self.input_rows if self.input_rows else 0.0

    def summary(self) -> str:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(self.rejection_reasons.items())
        )
        sheets = ", ".join(f"{name}={count}" for name, count in self.rows_per_sheet.items())
        return (
            f"input_rows={self.input_rows} ({sheets or 'none'}) parsed_rows={self.parsed_rows} "
            f"rejected_rows={self.rejected_rows} ({reasons or 'none'}) "
            f"parse_ratio={self.parse_ratio:.4f} quotable_items={self.quotable_items} "
            f"unpriced_items={self.unpriced_items} items_persisted={self.items_persisted}"
        )


def parse_catalogue(path: Path) -> tuple[dict[str, SupportItem], CatalogueLoadReport]:
    """Read both sheets and build one SupportItem per item number.

    The ``Current Support Items`` sheet is read first; a later row with an item number
    already seen (in either sheet) is rejected as ``duplicate_item_number`` so the current
    row wins over a legacy one. ``unpriced_items`` counts non-quotable items that still have
    no national limit (for example SIL items priced through the quote/roster process).
    """
    items: dict[str, SupportItem] = {}
    report = CatalogueLoadReport()
    for sheet_name, row in iter_catalogue_rows(path):
        report.input_rows += 1
        report.rows_per_sheet[sheet_name] += 1
        try:
            item = parse_catalogue_row(row, legacy=sheet_name == SHEET_LEGACY)
            if item.item_number in items:
                raise CatalogueRowError("duplicate_item_number")
        except CatalogueRowError as exc:
            report.rejected_rows += 1
            report.rejection_reasons[exc.reason] += 1
            continue
        report.parsed_rows += 1
        if item.quotable:
            report.quotable_items += 1
        elif item.limits.national is None:
            report.unpriced_items += 1
        items[item.item_number] = item
    return items, report


def load_catalogue(path: Path, repository: SupportItemRepository) -> CatalogueLoadReport:
    """Parse the catalogue at ``path`` and replace the repository contents with it."""
    items, report = parse_catalogue(path)
    if report.parse_ratio < MIN_PARSE_RATIO:
        raise CatalogueFormatError(
            f"only {report.parse_ratio:.1%} of {report.input_rows} rows parsed; "
            f"rejections: {dict(report.rejection_reasons)}"
        )
    repository.replace_all(items.values())
    report.items_persisted = len(items)
    logger.info("catalogue load: %s", report.summary())
    return report
