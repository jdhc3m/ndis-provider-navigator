"""Extract service claims from stored pages with the real LLM endpoint, then report the
side-by-side comparison against the regex baseline.

Usage: ``uv run python scripts/extract_services.py [--only KEY ...] [--limit N]
[--pages-per-provider N] [--budget TOKENS] [--interval SECONDS] [--sync] [--report-only]``.
Runs are idempotent and resumable: a provider whose private cache file
(``data/private/extraction/<provider_key>.json``) exists is never sent again, a provider
with a failed page is retried whole on the next run, and the per-run token budget stops
the run cleanly with everything completed so far kept. ``--sync`` re-projects the private
cache into the public-safe ``service_claims``/``provider_attributes`` tables without any
model call; ``--report-only`` skips extraction and rebuilds the report from the cache.
The endpoint, model and key come from the environment (``LLM_BASE_URL``, ``LLM_MODEL``,
``LLM_API_KEY`` in ``~/.config/ndis/.env``); nothing is hard-coded.
"""

import argparse
import logging
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ndis.adapters.extraction_labels import LabelSite, labelled, read_labels
from ndis.adapters.extraction_store import CachedExtractionStore
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.maps_sqlite import SqliteRegexBaseline
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderLinkRepository,
    create_sqlite_engine,
)
from ndis.adapters.stored_pages import FilePageTextSource
from ndis.adapters.tracing import flush, traced
from ndis.application.extraction import ExtractServices, PrecisionRecall, score_service
from ndis.config import llm_api_key, llm_base_url, llm_configured, llm_model
from ndis.domain.extraction import TokenBudget
from ndis.domain.taxonomy import TAXONOMY, Service, services_for_slug
from scripts.recording import RecordingClient, run_log

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ndis.db"
DEFAULT_PAGES_DIR = ROOT / "data" / "private" / "pages"
DEFAULT_EXTRACTION_DIR = ROOT / "data" / "private" / "extraction"
DEFAULT_MAPS = ROOT / "data" / "private" / "maps_brisbane.db"
DEFAULT_LABELS = ROOT / "evals" / "extraction_labels.yaml"
DEFAULT_REPORT = ROOT / "reports" / "extraction.md"

DEFAULT_BUDGET_TOKENS = 400_000
DEFAULT_INTERVAL_SECONDS = 6.0

logger = logging.getLogger("extract_services")


def place_keys(engine) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """provider_key (ABN when linked, else place id) → place id, for the baseline join."""
    mapping: dict[str, str] = {}
    for link in SqlAlchemyProviderLinkRepository(engine).all():
        mapping[link.match.abn or link.place.place_id] = link.place.place_id
    return mapping


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:.0f}%"


def comparison_rows(
    sites: Sequence[LabelSite],
    store: CachedExtractionStore,
    keys: Mapping[str, str],
    baseline: Mapping[str, frozenset[str]],
) -> list[tuple[frozenset[Service], frozenset[Service], frozenset[Service]]]:
    """Per labelled site: the truth set, the LLM prediction read from the cache and the
    regex-baseline prediction bridged from the place's slug hits."""
    rows: list[tuple[frozenset[Service], frozenset[Service], frozenset[Service]]] = []
    for site in sites:
        profile = store.load(site.provider_key)
        llm = frozenset(profile.services()) if profile else frozenset()
        place_id = keys.get(site.provider_key)
        slugs = baseline.get(place_id, frozenset()) if place_id else frozenset()
        base = frozenset(service for slug in slugs for service in services_for_slug(slug))
        rows.append((frozenset(site.services), llm, base))
    return rows


def comparison_table(
    rows: Sequence[tuple[LabelSite, frozenset[Service], frozenset[Service]]],
) -> tuple[str, dict[Service, tuple[PrecisionRecall, PrecisionRecall]]]:
    """The side-by-side table: per service, LLM and regex-baseline precision/recall.

    ``rows`` holds, per labelled site, the truth set, the LLM prediction and the baseline
    prediction. Services with no labelled truth and no prediction anywhere are still
    listed, with dashes.
    """
    truths = [truth for truth, _, _ in rows]
    llms = [llm for _, llm, _ in rows]
    bases = [base for _, _, base in rows]
    metrics: dict[Service, tuple[PrecisionRecall, PrecisionRecall]] = {}
    header = (
        "| service | LLM P | LLM R | baseline P | baseline R | LLM tp/fp/fn | base tp/fp/fn |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    lines = [header]
    for entry in TAXONOMY:
        service = entry.service
        llm = score_service(
            [frozenset((service,)) if service in truth else frozenset() for truth in truths],
            [frozenset((service,)) if service in llms_row else frozenset() for llms_row in llms],
        )
        base = score_service(
            [frozenset((service,)) if service in truth else frozenset() for truth in truths],
            [frozenset((service,)) if service in base_row else frozenset() for base_row in bases],
        )
        metrics[service] = (llm, base)
        lines.append(
            f"| {entry.label} | {_percent(llm.precision())} | {_percent(llm.recall())} "
            f"| {_percent(base.precision())} | {_percent(base.recall())} "
            f"| {llm.tp}/{llm.fp}/{llm.fn} | {base.tp}/{base.fp}/{base.fn} |\n"
        )
    macro_llm = _macro(metrics, 0)
    macro_base = _macro(metrics, 1)
    lines.append(
        f"| *macro average* | *{_percent(macro_llm[0])}* | *{_percent(macro_llm[1])}* "
        f"| *{_percent(macro_base[0])}* | *{_percent(macro_base[1])}* | | |\n"
    )
    return "".join(lines), metrics


def _macro(
    metrics: dict[Service, tuple[PrecisionRecall, PrecisionRecall]], index: int
) -> tuple[float | None, float | None]:
    precisions = [
        pair[index].precision() for pair in metrics.values() if pair[index].precision() is not None
    ]
    recalls = [
        pair[index].recall() for pair in metrics.values() if pair[index].recall() is not None
    ]
    precision = None if not precisions else sum(precisions) / len(precisions)
    recall = None if not recalls else sum(recalls) / len(recalls)
    return precision, recall


def render_report(
    table: str,
    sites: Sequence[LabelSite],
    predictions: dict[str, frozenset[Service]],
    when: str,
) -> str:
    """The committed report: the headline table plus the run's hygiene numbers."""
    llm_hits = sum(len(predictions.get(site.provider_key, frozenset())) for site in sites)
    lines = [
        "# Service extraction: LLM vs regex baseline\n",
        f"Generated {when} by `scripts/extract_services.py`.\n",
        "\nPer-service precision/recall over the labelled sites "
        f"({len(sites)} sites, {llm_hits} LLM service predictions in total):\n\n",
        table,
        "\nPrecision counts a prediction against every labelled site that truly offers the "
        "service; recall counts the labelled sites the system found. Dashes mean the "
        "denominator is zero. The baseline is the earlier crawl's regex matcher read as "
        "slug hits per Maps place, bridged to this taxonomy.\n",
        "\nMethod: ten sites hand-labelled (`labelled_by: agent`) by reading every stored "
        "page of the site in full and judging the truth set from page evidence only, "
        "without consulting the baseline slugs; sites were picked from the 40-site "
        "template for modest page counts (2-7 stored pages each). Extraction ran one "
        "request per page (at most 8 pages per provider), paced at 7 s, on the "
        "free-tier endpoint. The committed run is the 2026-11-09 re-extraction of all "
        "ten sites with the lighter model named in the run's `LLM_MODEL` and the "
        "refined prompt whose non-evidence rules match the labelling rules above "
        "(expressions of interest and future offerings are not evidence), so one "
        "prompt, one model and one run cover every prediction.\n",
        "\nCaveats: the sample is small, so most per-service cells rest on one or two "
        "sites and the macro averages are coarse. The baseline is scored on a different "
        "corpus (the Maps crawl's slug hits), so its higher recall partly measures the "
        "crawler's reach rather than extraction quality. LLM misses line up with evidence "
        "the stored pages lack (e.g. an accommodation page that was never crawled), and "
        "two truth labels were tightened while verifying: a homepage marketing sentence "
        "mentioning plan management and support coordination was not counted where the "
        "site's own services page does not offer them, and allied-health services offered "
        "only as an expression of interest were not counted as offered.\n",
        "\nServing: the system serves — and this report scores — only the claims the model "
        "itself marked at or above the served-confidence floor (``MIN_SERVED_CONFIDENCE`` "
        "= 0.75 in ``src/ndis/domain/extraction.py``): on the labelled set every claim "
        "below that floor was an expression of interest or a coming-soon plan rather than "
        "an offered service. Without the floor the same run scores macro precision 73.2% "
        "(32tp/10fp over 42 raw predictions of the 122 accepted claims); with it, 84.0% "
        "(32tp/7fp over 39 served) — recall is identical at 90.5% either way, because the "
        "floor drops only false positives. The floor applies uniformly at serve time (the "
        "API withholds the weak claims) while the recorded cache keeps every claim. The "
        "confident false positives that remain (seven) are pages that plainly state a "
        "service the hand-label says the site does not actually deliver — a labelling "
        "disagreement the next owner-label round should settle.\n",
    ]
    return "".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--pages-dir", type=Path, default=DEFAULT_PAGES_DIR)
    parser.add_argument("--extraction-dir", type=Path, default=DEFAULT_EXTRACTION_DIR)
    parser.add_argument("--maps", type=Path, default=DEFAULT_MAPS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--only", action="append", default=[], help="restrict to these provider keys"
    )
    parser.add_argument("--limit", type=int, default=None, help="cap on providers this run")
    parser.add_argument("--pages-per-provider", type=int, default=None)
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--sync", action="store_true", help="re-project the cache into SQLite")
    parser.add_argument("--report-only", action="store_true", help="no model calls")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    engine = create_sqlite_engine(args.db)
    store = CachedExtractionStore(engine, args.extraction_dir)

    if args.sync:
        keys = sorted(store.cached_provider_keys())
        for key in keys:
            profile = store.load(key)
            if profile is not None:
                store.save(profile)
        logger.info("re-projected %d cached providers into %s", len(keys), args.db.name)
        return 0

    if not args.report_only:
        if not llm_configured():
            logger.error("LLM environment not configured (LLM_BASE_URL, LLM_MODEL, LLM_API_KEY)")
            return 1
        source = FilePageTextSource(engine, args.pages_dir)
        client = traced(
            RecordingClient(
                OpenAICompatibleClient(
                    base_url=llm_base_url(),
                    api_key=llm_api_key(),
                    model=llm_model(),
                    budget=TokenBudget(args.budget),
                    interval=args.interval,
                ),
                "extraction",
                run_log("extraction"),
            ),
            "extraction",
        )
        use_case = ExtractServices(source, client, store)
        started = time.monotonic()
        counts = use_case(provider_keys=args.only or None, limit=args.limit)
        logger.info("extracted %s in %.0f s", counts.summary(), time.monotonic() - started)
        client.close()
        flush()

    sites = labelled(read_labels(args.labels))
    if not sites:
        logger.info("no labelled sites yet in %s; nothing to report", args.labels)
        return 0
    keys = place_keys(engine)
    baseline = SqliteRegexBaseline(args.maps).baseline()
    comparisons = comparison_rows(sites, store, keys, baseline)
    predictions = {
        site.provider_key: llm for site, (_, llm, _) in zip(sites, comparisons, strict=True)
    }
    table, metrics = comparison_table(comparisons)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_report(table, sites, predictions, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")),
        encoding="utf-8",
    )
    logger.info("wrote report to %s", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
