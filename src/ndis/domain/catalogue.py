"""Pure parsing and price-limit rules for rows of the NDIS Support Catalogue 2026-27 workbook.

The 2026-27 catalogue publishes one ``National`` price limit plus ``Remote`` and ``Very
Remote`` tiers; unlike earlier catalogues it has no per-state columns, so every state shares
the national limit. The state is still validated so a caller cannot ask for a nonsense
jurisdiction and get a number back.
"""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ndis.domain.entities import PriceLimits, Remoteness, SupportItem
from ndis.domain.register import STATES

CATALOGUE_TITLE = "NDIS Support Catalogue 2026-27"
FINANCIAL_YEAR = "2026-27"
FINANCIAL_YEAR_START = date(2026, 7, 1)
FINANCIAL_YEAR_END = date(2027, 6, 30)

SHEET_CURRENT = "Current Support Items"
SHEET_LEGACY = "Legacy Support Items"
CATALOGUE_SHEETS = (SHEET_CURRENT, SHEET_LEGACY)

COLUMN_ITEM_NUMBER = "Support Item Number"
COLUMN_ITEM_NAME = "Support Item Name"
COLUMN_GROUP_NUMBER = "Registration Group Number"
COLUMN_GROUP_NAME = "Registration Group Name"
COLUMN_UNIT = "Unit"
COLUMN_QUOTE = "Quote"
COLUMN_START_DATE = "Start date"
COLUMN_END_DATE = "End Date"
COLUMN_NATIONAL = "National"
COLUMN_REMOTE = "Remote"
COLUMN_VERY_REMOTE = "Very Remote"

CATALOGUE_COLUMNS = (
    COLUMN_ITEM_NUMBER,
    COLUMN_ITEM_NAME,
    COLUMN_GROUP_NUMBER,
    COLUMN_GROUP_NAME,
    COLUMN_UNIT,
    COLUMN_QUOTE,
    COLUMN_START_DATE,
    COLUMN_END_DATE,
    COLUMN_NATIONAL,
    COLUMN_REMOTE,
    COLUMN_VERY_REMOTE,
)

OPEN_ENDED_DATE = "99991231"
CENTS = Decimal("0.01")

type CellValue = str | int | float | None


class CatalogueRowError(ValueError):
    """A catalogue row that cannot be turned into a SupportItem; ``reason`` is a short slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _text(value: CellValue) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_money(value: CellValue) -> Decimal | None:
    """Turn a price cell into whole cents; blank cells mean no published limit.

    The workbook stores prices as IEEE doubles (``271.58999999999997`` for 271.59), and
    ``str()`` of the parsed float gives the shortest round-trip text (``271.59``), which is
    exact in cents for every published price. A value that is not a non-negative amount in
    whole cents is rejected rather than rounded.
    """
    text = _text(value)
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise CatalogueRowError("invalid_price") from exc
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(CENTS):
        raise CatalogueRowError("invalid_price")
    return amount.quantize(CENTS)


def parse_catalogue_date(value: CellValue) -> date | None:
    """Parse a ``YYYYMMDD`` cell; ``99991231`` means open-ended and yields ``None``."""
    text = _text(value)
    if text == OPEN_ENDED_DATE:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise CatalogueRowError("invalid_date") from exc


def parse_catalogue_row(row: Mapping[str, CellValue], *, legacy: bool) -> SupportItem:
    """Turn one catalogue row into a SupportItem.

    Raises ``CatalogueRowError`` with a reason slug when a required field is missing or
    malformed, or when the row's start date lies outside the 2026-27 financial year, which
    is how an older catalogue is prevented from silently standing in for the current one.
    """
    item_number = _text(row.get(COLUMN_ITEM_NUMBER))
    if not item_number:
        raise CatalogueRowError("missing_item_number")
    name = _text(row.get(COLUMN_ITEM_NAME))
    if not name:
        raise CatalogueRowError("missing_item_name")
    group_number = _text(row.get(COLUMN_GROUP_NUMBER))
    group_name = _text(row.get(COLUMN_GROUP_NAME))
    if not group_number or not group_name:
        raise CatalogueRowError("missing_registration_group")
    unit = _text(row.get(COLUMN_UNIT))
    if not unit:
        raise CatalogueRowError("missing_unit")
    quote = _text(row.get(COLUMN_QUOTE)).lower()
    if quote not in {"yes", "no"}:
        raise CatalogueRowError("invalid_quote_flag")
    start_date = parse_catalogue_date(row.get(COLUMN_START_DATE))
    if start_date is None or not FINANCIAL_YEAR_START <= start_date <= FINANCIAL_YEAR_END:
        raise CatalogueRowError("start_date_outside_financial_year")
    limits = PriceLimits(
        national=parse_money(row.get(COLUMN_NATIONAL)),
        remote=parse_money(row.get(COLUMN_REMOTE)),
        very_remote=parse_money(row.get(COLUMN_VERY_REMOTE)),
    )
    return SupportItem(
        item_number=item_number,
        name=name,
        registration_group_number=group_number,
        registration_group_name=group_name,
        unit=unit,
        quotable=quote == "yes",
        start_date=start_date,
        end_date=parse_catalogue_date(row.get(COLUMN_END_DATE)),
        limits=limits,
        legacy=legacy,
    )


def price_limit(item: SupportItem, state: str, remoteness: Remoteness) -> Decimal | None:
    """The published maximum price of ``item`` for a participant in ``state`` at ``remoteness``.

    Returns ``None`` when the catalogue publishes no limit (quotable or unpriced items).
    Every state shares the national limit in 2026-27; an unknown state raises ``ValueError``.
    """
    if state not in STATES:
        raise ValueError(f"state must be one of {sorted(STATES)}")
    return item.limits.for_remoteness(remoteness)
