"""Load ``data/public/support_catalogue.xlsx`` into the SQLite database and log the counts.

Usage: ``uv run python scripts/load_catalogue.py [--xlsx PATH] [--db PATH]``.
Fetch the workbook first with ``uv run python scripts/fetch_public_data.py --only catalogue``.
"""

import argparse
import logging
import sys
from pathlib import Path

from ndis.adapters.catalogue_xlsx import CatalogueFormatError, load_catalogue
from ndis.adapters.sqlite_repository import SqlAlchemySupportItemRepository, create_sqlite_engine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = ROOT / "data" / "public" / "support_catalogue.xlsx"
DEFAULT_DB = ROOT / "data" / "ndis.db"

logger = logging.getLogger("load_catalogue")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX, help="catalogue XLSX to load")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database to write")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.xlsx.is_file():
        logger.error("catalogue XLSX not found at %s; run scripts/fetch_public_data.py", args.xlsx)
        return 1
    repository = SqlAlchemySupportItemRepository(create_sqlite_engine(args.db))
    try:
        report = load_catalogue(args.xlsx, repository)
    except CatalogueFormatError as exc:
        logger.error("catalogue load failed: %s", exc)
        return 1
    logger.info("loaded %s into %s: %s", args.xlsx, args.db, report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
