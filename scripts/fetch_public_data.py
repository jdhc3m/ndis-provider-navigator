"""Download public source data into the ignored ``data/public/`` directory.

Each dataset has one ``fetch_<name>`` function; ``--only`` selects a subset. Downloads
are verified after writing and deleted when they fail verification, so a truncated
file never masquerades as the real thing.
"""

import argparse
import logging
import shutil
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

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

logger = logging.getLogger("fetch_public_data")


def download(url: str, destination: Path) -> int:
    """Stream ``url`` to ``destination`` with a browser-like User-Agent; return the size."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
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
    logger.info("downloading %s -> %s", REGISTER_URL, destination)
    size = download(REGISTER_URL, destination)
    logger.info("downloaded %d bytes", size)
    problems = verify_register(destination, REGISTER_MIN_BYTES, REGISTER_MIN_ABNS)
    if problems:
        destination.unlink(missing_ok=True)
        raise SystemExit("register download rejected and deleted:\n  " + "\n  ".join(problems))
    logger.info("register verified: %s", destination)
    return destination


DATASETS: dict[str, Callable[[], Path]] = {"register": fetch_register}


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
