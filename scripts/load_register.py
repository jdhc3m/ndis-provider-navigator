"""Load ``data/public/register.csv`` into the SQLite database and log the counts.

Usage: ``uv run python scripts/load_register.py [--csv PATH] [--db PATH]``.
Fetch the CSV first with ``uv run python scripts/fetch_public_data.py --only register``.
"""

import argparse
import logging
import sys
from pathlib import Path

from ndis.adapters.register_csv import RegisterFormatError, load_register
from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository, create_sqlite_engine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "data" / "public" / "register.csv"
DEFAULT_DB = ROOT / "data" / "ndis.db"

logger = logging.getLogger("load_register")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="register CSV to load")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database to write")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.csv.is_file():
        logger.error("register CSV not found at %s; run scripts/fetch_public_data.py", args.csv)
        return 1
    repository = SqlAlchemyProviderRepository(create_sqlite_engine(args.db))
    try:
        report = load_register(args.csv, repository)
    except RegisterFormatError as exc:
        logger.error("register load failed: %s", exc)
        return 1
    logger.info("loaded %s into %s: %s", args.csv, args.db, report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
