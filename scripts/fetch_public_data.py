"""Download public source data into the ignored ``data/public/`` directory.

Each dataset has one ``fetch_<name>`` function; ``--only`` selects a subset. Downloads
go to a ``.part`` file that is verified and only then renamed into place, and deleted
when the download or verification fails, so a truncated file never masquerades as the
real thing.
"""

import argparse
import logging
import shutil
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ndis.adapters.catalogue_xlsx import CatalogueFormatError, count_distinct_items
from ndis.adapters.pricing_pdf import PricingPdfError, count_pdf_pages
from ndis.adapters.register_csv import RegisterFormatError, count_distinct_abns

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT / "data" / "public"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

REGISTER_URL = (
    "https://www.ndiscommission.gov.au/provider-registration/find-registered-provider/download-csv"
)
REGISTER_MIN_BYTES = 30 * 1024 * 1024
REGISTER_MIN_ABNS = 25_000

# "NDIS Support Catalogue 2026-27" (XLSX), the only catalogue download linked from
# https://www.ndis.gov.au/providers/pricing-and-payments/pricing/what-support-catalogue
# (the Pricing Arrangements page links to that page). The server names the file
# ``NDIS-Support-Catalogue-2026-27 v1_1.xlsx``; the media id changes with each new version,
# so update this constant and the README when a later 2026-27 release is published.
CATALOGUE_URL = "https://www.ndis.gov.au/media/8038/download?attachment"
CATALOGUE_MIN_BYTES = 100 * 1024
CATALOGUE_MIN_ITEMS = 900

# "NDIS Pricing Arrangements and Price Limits 2025-26 v1.1" (PDF), linked from
# https://www.ndis.gov.au/providers/pricing-and-payments/pricing/pricing-arrangements
# under "Other pricing and claiming guidance documents" (the 2026-27 "pricing schedules"
# are price tables only; the written pricing rules live in this document). The media id
# changes with each new version, so update this constant and the README when a later
# release is published.
PRICING_PDF_URL = "https://www.ndis.gov.au/media/8096/download?attachment"
PRICING_MIN_BYTES = 500 * 1024
PRICING_MIN_PAGES = 50

logger = logging.getLogger("fetch_public_data")


def download(url: str, destination: Path) -> int:
    """Stream ``url`` to ``destination`` with a browser-like User-Agent; return the size."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        filename = response.headers.get("Content-Disposition")
        if filename:
            logger.info("server names the file: %s", filename)
        shutil.copyfileobj(response, out, length=1024 * 1024)
    return destination.stat().st_size


def verify_register(
    path: Path, min_bytes: int = REGISTER_MIN_BYTES, min_abns: int = REGISTER_MIN_ABNS
) -> list[str]:
    """Return the reasons ``path`` is not a complete register download (empty = OK)."""
    problems: list[str] = []
    size = path.stat().st_size
    if size <= min_bytes:
        problems.append(f"file is {size} bytes; a full register exceeds {min_bytes} bytes")
    try:
        abns = count_distinct_abns(path)
    except (RegisterFormatError, UnicodeDecodeError) as exc:
        return problems + [f"file is not a register CSV: {exc}"]
    if abns <= min_abns:
        problems.append(f"file has {abns} distinct ABNs; a full register exceeds {min_abns}")
    return problems


def fetch_register(public_dir: Path = PUBLIC_DIR) -> Path:
    """Download the NDIS Commission provider register and verify it is complete."""
    destination = public_dir / "register.csv"
    partial = destination.with_suffix(".part")
    logger.info("downloading %s -> %s", REGISTER_URL, partial)
    try:
        size = download(REGISTER_URL, partial)
        logger.info("downloaded %d bytes", size)
        problems = verify_register(partial, REGISTER_MIN_BYTES, REGISTER_MIN_ABNS)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if problems:
        partial.unlink(missing_ok=True)
        raise SystemExit("register download rejected and deleted:\n  " + "\n  ".join(problems))
    partial.replace(destination)
    logger.info("register verified: %s", destination)
    return destination


def verify_catalogue(
    path: Path, min_bytes: int = CATALOGUE_MIN_BYTES, min_items: int = CATALOGUE_MIN_ITEMS
) -> list[str]:
    """Return the reasons ``path`` is not a complete 2026-27 catalogue download (empty = OK).

    Only rows whose start date falls in 2026-27 count as items, so an HTML error page, a
    truncated workbook and an older catalogue are all rejected.
    """
    problems: list[str] = []
    size = path.stat().st_size
    if size <= min_bytes:
        problems.append(f"file is {size} bytes; a full catalogue exceeds {min_bytes} bytes")
    try:
        items = count_distinct_items(path)
    except CatalogueFormatError as exc:
        return problems + [f"file is not a support catalogue workbook: {exc}"]
    if items <= min_items:
        problems.append(
            f"file has {items} distinct 2026-27 support items; a full catalogue exceeds {min_items}"
        )
    return problems


def fetch_catalogue(public_dir: Path = PUBLIC_DIR) -> Path:
    """Download the NDIS Support Catalogue 2026-27 workbook and verify it is complete."""
    destination = public_dir / "support_catalogue.xlsx"
    partial = destination.with_suffix(".part")
    logger.info("downloading %s -> %s", CATALOGUE_URL, partial)
    try:
        size = download(CATALOGUE_URL, partial)
        logger.info("downloaded %d bytes", size)
        problems = verify_catalogue(partial, CATALOGUE_MIN_BYTES, CATALOGUE_MIN_ITEMS)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if problems:
        partial.unlink(missing_ok=True)
        raise SystemExit("catalogue download rejected and deleted:\n  " + "\n  ".join(problems))
    partial.replace(destination)
    logger.info("catalogue verified: %s", destination)
    return destination


def verify_pricing_pdf(path: Path) -> list[str]:
    """Return the reasons ``path`` is not the complete Pricing Arrangements PDF (empty = OK)."""
    problems: list[str] = []
    size = path.stat().st_size
    if size <= PRICING_MIN_BYTES:
        problems.append(f"file is {size} bytes; the pricing PDF exceeds {PRICING_MIN_BYTES} bytes")
    if not path.read_bytes()[:5] == b"%PDF-":
        return problems + ["file does not start with the %PDF magic bytes"]
    try:
        pages = count_pdf_pages(path)
    except PricingPdfError as exc:
        return problems + [f"file is not a readable PDF: {exc}"]
    if pages < PRICING_MIN_PAGES:
        problems.append(
            f"PDF has {pages} pages; the pricing arrangements document has at least "
            f"{PRICING_MIN_PAGES}"
        )
    return problems


def fetch_pricing_pdf(public_dir: Path = PUBLIC_DIR) -> Path:
    """Download the NDIS Pricing Arrangements and Price Limits PDF and verify it is complete."""
    destination = public_dir / "pricing_arrangements.pdf"
    partial = destination.with_suffix(".part")
    logger.info("downloading %s -> %s", PRICING_PDF_URL, partial)
    try:
        size = download(PRICING_PDF_URL, partial)
        logger.info("downloaded %d bytes", size)
        problems = verify_pricing_pdf(partial)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if problems:
        partial.unlink(missing_ok=True)
        raise SystemExit("pricing PDF download rejected and deleted:\n  " + "\n  ".join(problems))
    partial.replace(destination)
    logger.info("pricing PDF verified: %s", destination)
    return destination


DATASETS: dict[str, Callable[[], Path]] = {
    "catalogue": fetch_catalogue,
    "pricing_pdf": fetch_pricing_pdf,
    "register": fetch_register,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(DATASETS),
        help="fetch only the named dataset(s); default fetches all",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for name in args.only or sorted(DATASETS):
        DATASETS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
