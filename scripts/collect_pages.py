"""Collect website evidence for linked providers: seed the page cache from the private Maps
crawl, then politely fetch the websites of providers that have no cached page.

Usage: ``uv run python scripts/collect_pages.py [--maps PATH] [--db PATH] [--pages-dir PATH]
[--refresh] [--limit N] [--dry-run]``. Run ``scripts/resolve_entities.py`` first so
``provider_links`` exists. Page text goes under the ignored ``data/private/pages/``; only
``page_meta`` (url, fetched_at, status, text_hash) lands in the database. Crawl pages the
database already holds are not seeded again, so live results survive later runs.
``--refresh`` fetches every provider's website again, ``--limit`` caps the live fetches and
``--dry-run`` writes nothing: it counts what seeding would store and lists the fetches.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from ndis.adapters.maps_sqlite import MapsExportError, SqliteMapsPageSource
from ndis.adapters.sqlite_repository import (
    FilePageStore,
    SqlAlchemyProviderSiteSource,
    create_sqlite_engine,
)
from ndis.adapters.web_pages import HttpxPageFetcher, TrafilaturaExtractor
from ndis.application.pages import CollectPages

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPS = ROOT / "data" / "private" / "maps_brisbane.db"
DEFAULT_DB = ROOT / "data" / "ndis.db"
DEFAULT_PAGES_DIR = ROOT / "data" / "private" / "pages"

logger = logging.getLogger("collect_pages")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps", type=Path, default=DEFAULT_MAPS, help="private Maps export")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database to write")
    parser.add_argument(
        "--pages-dir", type=Path, default=DEFAULT_PAGES_DIR, help="private directory for text"
    )
    parser.add_argument("--refresh", action="store_true", help="fetch cached providers again")
    parser.add_argument("--limit", type=int, default=None, help="cap on live fetches")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write nothing; count the seeding and list the live fetches",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.maps.is_file():
        logger.error("Maps export not found at %s; nothing to seed from", args.maps)
        return 1
    if not args.db.is_file():
        logger.error("database not found at %s; run scripts/resolve_entities.py", args.db)
        return 1
    engine = create_sqlite_engine(args.db)
    fetcher = HttpxPageFetcher()
    use_case = CollectPages(
        SqlAlchemyProviderSiteSource(engine),
        SqliteMapsPageSource(args.maps),
        fetcher,
        TrafilaturaExtractor(),
        FilePageStore(engine, args.pages_dir),
    )
    started = time.monotonic()
    try:
        result = use_case(refresh=args.refresh, limit=args.limit, dry_run=args.dry_run)
    except MapsExportError as exc:
        logger.error("page collection failed: %s", exc)
        return 1
    finally:
        fetcher.close()
    for url in result.planned:
        logger.info("would fetch %s", url)
    logger.info("collected %s in %.0f s", result.summary(), time.monotonic() - started)
    logger.info("page text is under %s; metadata in %s", args.pages_dir, args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
