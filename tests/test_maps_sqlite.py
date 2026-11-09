"""Maps export adapters (places and crawled pages) on a temp SQLite file with the real
schema, and the rapidfuzz scorer."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ndis.adapters.fuzzy import RapidfuzzScorer
from ndis.adapters.maps_sqlite import (
    MapsExportError,
    SqliteMapsPageSource,
    SqliteMapsPlaceSource,
)
from ndis.domain.entity_resolution import FUZZY_THRESHOLD, MapsPlace
from ndis.domain.pages import CachedPage


def test_places_are_read_in_place_id_order_with_normalised_fields(maps_export: Path) -> None:
    places = SqliteMapsPlaceSource(maps_export).places()
    assert [place.place_id for place in places] == [
        "place-alpha",
        "place-beta",
        "place-delta",
        "place-recruit",
        "place-unknown",
    ]
    assert places[0] == MapsPlace(
        place_id="place-alpha",
        title="Alpha Therapy - NDIS Provider Brisbane",
        category="Physiotherapist",
        website="https://www.10000000001.example.com.au/about?utm_source=gbp",
        website_domain="10000000001.example.com.au",
        suburb="West End",
        postcode="4101",
        latitude=-27.48,
        longitude=153.01,
    )
    beta, delta, _, unknown = places[1:]
    assert (beta.website, beta.website_domain) == (None, None)
    assert (delta.category, delta.latitude, delta.longitude) == (None, None, None)
    assert (unknown.suburb, unknown.postcode) == (None, None)


def test_missing_or_foreign_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(MapsExportError, match="not found"):
        SqliteMapsPlaceSource(tmp_path / "missing.db").places()
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("create table unrelated (x)").connection.close()
    with pytest.raises(MapsExportError, match="not a Maps export"):
        SqliteMapsPlaceSource(other).places()


def test_export_is_opened_read_only(maps_export: Path) -> None:
    before = maps_export.read_bytes()
    SqliteMapsPlaceSource(maps_export).places()
    assert maps_export.read_bytes() == before
    assert not (maps_export.parent / "maps_brisbane.db-journal").exists()


@pytest.mark.parametrize(
    ("query", "candidate", "above"),
    [
        ("lifest healthcare", "lifest health care", True),
        ("vitium cura", "vitium cura", True),
        ("ndis support coordination", "isupport coordination", False),
        ("disability care", "brisbane disability care", False),
        ("", "anything", False),
    ],
)
def test_rapidfuzz_scorer_is_plain_ratio(query: str, candidate: str, above: bool) -> None:
    score = RapidfuzzScorer().score(query, candidate)
    assert 0.0 <= score <= 100.0
    assert (score >= FUZZY_THRESHOLD) is above


def test_only_ok_pages_are_read_with_text_and_timestamps_as_given(maps_export: Path) -> None:
    pages = SqliteMapsPageSource(maps_export).ok_pages()
    assert [(page.place_id, page.url) for page in pages] == [
        ("place-alpha", "https://www.10000000001.example.com.au/"),
        ("place-alpha", "https://www.10000000001.example.com.au/about"),
        ("place-beta", "https://beta.example/"),
        ("place-recruit", "https://hireco.example.com/"),
    ]
    home, about, beta, _ = pages
    assert home == CachedPage(
        place_id="place-alpha",
        url="https://www.10000000001.example.com.au/",
        title="Alpha Therapy",
        text="Alpha Therapy\nPhysiotherapy and occupational therapy for NDIS participants.\n\n\n",
        fetched_at=datetime(2026, 11, 7, 3, 49, 42, tzinfo=UTC),
    )
    assert about.title is None
    # Blank title and whitespace text come through as None and as-is; the use case decides.
    assert (beta.title, beta.text) == (None, "  \n ")


def test_page_source_reports_missing_or_foreign_files_and_stays_read_only(
    tmp_path: Path, maps_export: Path
) -> None:
    with pytest.raises(MapsExportError, match="not found"):
        SqliteMapsPageSource(tmp_path / "missing.db").ok_pages()
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("create table unrelated (x)").connection.close()
    with pytest.raises(MapsExportError, match="not a Maps export"):
        SqliteMapsPageSource(other).ok_pages()
    before = maps_export.read_bytes()
    SqliteMapsPageSource(maps_export).ok_pages()
    assert maps_export.read_bytes() == before
