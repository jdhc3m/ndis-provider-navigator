"""Extraction eval: the labelled sites scored against the recorded LLM run and the regex
baseline, through the same comparison the report script renders.

No model call is made: the per-provider cache under ``data/private/extraction`` is the
recorded run, so this is cache-only and repeatable. It skips visibly while any labelled
site has no cache file yet (the extraction run has not covered it).

The macro precision is asserted against the owned floor in ``thresholds.yaml``; the
recall and baseline pins are the measured snapshot of the 2026-11-09 run over the ten
labelled sites (see ``reports/extraction.md``).
"""

from pathlib import Path

import pytest

from ndis.adapters.extraction_labels import labelled, read_labels
from ndis.adapters.extraction_store import CachedExtractionStore
from ndis.adapters.maps_sqlite import SqliteRegexBaseline
from ndis.adapters.sqlite_repository import create_sqlite_engine
from ndis.adapters.thresholds import read_thresholds
from scripts.extract_services import (
    DEFAULT_DB,
    DEFAULT_EXTRACTION_DIR,
    DEFAULT_LABELS,
    DEFAULT_MAPS,
    _macro,
    comparison_rows,
    comparison_table,
    place_keys,
)

THRESHOLDS = Path(__file__).with_name("thresholds.yaml")


@pytest.mark.eval
def test_extraction_per_service_metrics_on_labelled_sites() -> None:
    sites = labelled(read_labels(DEFAULT_LABELS))
    assert sites, "no agent-labelled sites in the extraction label template"
    store = CachedExtractionStore(create_sqlite_engine(DEFAULT_DB), DEFAULT_EXTRACTION_DIR)
    missing = [site.provider_key for site in sites if store.load(site.provider_key) is None]
    if missing:
        pytest.skip(f"{len(missing)} of {len(sites)} labelled sites lack an extraction cache")

    engine = create_sqlite_engine(DEFAULT_DB)
    baseline = SqliteRegexBaseline(DEFAULT_MAPS).baseline()
    rows = comparison_rows(sites, store, place_keys(engine), baseline)
    table, metrics = comparison_table(rows)

    # Every labelled site contributes a row, and every taxonomy service is reported.
    assert len(rows) == len(sites)
    assert len(table.splitlines()) == len(metrics) + 3  # header, rule, one row per service, macro

    llm_p, llm_r = _macro(metrics, 0)
    base_p, base_r = _macro(metrics, 1)
    assert llm_p is not None and llm_r is not None
    assert base_p is not None and base_r is not None
    thresholds = read_thresholds(THRESHOLDS)
    assert llm_p >= thresholds.extraction_precision, (
        f"extraction precision {llm_p:.3f} is below the owned gate "
        f"{thresholds.extraction_precision} (reports/extraction.md)"
    )
    # Measured 2026-11-09 over the ten labelled sites (reports/extraction.md); the recall
    # and baseline pins are the recorded snapshot the threshold sits on top of.
    assert round(100 * llm_r) == 90
    assert round(100 * base_p) == 56
    assert round(100 * base_r) == 93
    # The recorded run should beat the baseline on precision without trailing it on recall
    # by more than a few points; a regression in either direction shows up here.
    assert llm_p > base_p
    assert llm_r >= base_r - 0.10
