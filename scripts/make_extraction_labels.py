"""Write the 40-site extraction-labels template from the resolved links and stored pages.

Usage: ``uv run python scripts/make_extraction_labels.py [--db PATH] [--labels PATH]
[--count N] [--seed N]``. Candidates are Maps places the matcher linked to a registered
provider (the ABN keys the page cache and the regex baseline are expressed in) that have
at least one ``ok`` page stored; the sample is drawn with a fixed seed so a regeneration
keeps the same sites, and labels already filled in the labels file are kept for rows with
the same key. The template ships with empty ``services`` and ``labelled_by``: a person
labels a site only after reading its pages.
"""

import argparse
import logging
import random
import sys
from pathlib import Path

from sqlalchemy import Engine, create_engine, func, select

from ndis.adapters.extraction_labels import LabelSite, merge_labels, read_labels, write_labels
from ndis.adapters.sqlite_repository import PageMetaRow, ProviderLinkRow
from ndis.domain.pages import FetchStatus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ndis.db"
DEFAULT_LABELS = ROOT / "evals" / "extraction_labels.yaml"
COUNT = 40
SEED = 20261109

logger = logging.getLogger("make_extraction_labels")


def _registered_sites_with_pages(engine: Engine) -> list[tuple[str, str, str]]:
    """(place_id, provider_key, title) of registered links with at least one ``ok`` page."""
    ok_counts = (
        select(
            PageMetaRow.provider_key,
            func.count().label("pages"),
        )
        .where(PageMetaRow.status == FetchStatus.OK.value)
        .group_by(PageMetaRow.provider_key)
        .subquery()
    )
    statement = (
        select(
            ProviderLinkRow.place_id,
            ProviderLinkRow.abn,
            ProviderLinkRow.title,
        )
        .join(ok_counts, ok_counts.c.provider_key == ProviderLinkRow.abn)
        .where(ProviderLinkRow.match_status == "registered", ok_counts.c.pages >= 1)
        .order_by(ProviderLinkRow.place_id)
    )
    with engine.connect() as connection:
        return [
            (str(place_id), str(abn), str(title))
            for place_id, abn, title in connection.execute(statement)
        ]


def sample_sites(
    candidates: list[tuple[str, str, str]], count: int, seed: int
) -> list[tuple[str, str, str]]:
    """A deterministic sample of candidate sites, stable in the candidate order."""
    if count >= len(candidates):
        return sorted(candidates)
    rng = random.Random(seed)
    return sorted(rng.sample(candidates, count))


def label_rows(rows: list[tuple[str, str, str]]) -> list[LabelSite]:
    from hashlib import sha256

    def key(place_id: str) -> str:
        return sha256(place_id.encode("utf-8")).hexdigest()[:12]

    return [
        LabelSite(
            key=key(place_id),
            maps_title=title,
            provider_key=provider_key,
            services=(),
            labelled_by=None,
        )
        for place_id, provider_key, title in rows
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="database to read")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="labels YAML")
    parser.add_argument("--count", type=int, default=COUNT, help="template size")
    parser.add_argument("--seed", type=int, default=SEED, help="sample seed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.db.is_file():
        logger.error("database not found at %s; run scripts/resolve_entities.py", args.db)
        return 1
    engine = create_engine(f"sqlite:///{args.db}")
    candidates = _registered_sites_with_pages(engine)
    rows = sample_sites(candidates, args.count, args.seed)
    sites = label_rows(rows)
    previous = read_labels(args.labels) if args.labels.is_file() else ()
    sites = list(merge_labels(sites, previous))
    write_labels(args.labels, sites, args.seed)
    labelled = sum(site.labelled_by is not None for site in sites)
    logger.info(
        "wrote %d template rows (%d labelled, %d kept) to %s",
        len(sites),
        labelled,
        sum(site.labelled_by is not None for site in previous),
        args.labels,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
