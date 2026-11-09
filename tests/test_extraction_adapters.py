"""The extraction adapters: reading stored page text back, the private JSON cache plus its
public-safe projection, and the regex baseline reader over the Maps export."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from ndis.adapters.extraction_store import CachedExtractionStore, ExtractionCacheError
from ndis.adapters.maps_sqlite import MapsExportError, SqliteRegexBaseline
from ndis.adapters.sqlite_repository import (
    FilePageStore,
    ProviderAttributeRow,
    ServiceClaimRow,
    create_sqlite_engine,
)
from ndis.adapters.stored_pages import FilePageTextSource
from ndis.domain.extraction import (
    AgeGroup,
    PageAttributes,
    PageClaim,
    ProviderExtraction,
    ServiceClaim,
)
from ndis.domain.pages import FetchStatus, PageMeta, PageSource, text_hash, text_path
from ndis.domain.taxonomy import Service
from tests.conftest import MAPS_PAGES, MAPS_ROWS, write_maps_export


def make_profile(provider_key: str, when: datetime) -> ProviderExtraction:
    quote = "We provide physiotherapy and exercise programs for NDIS participants"
    return ProviderExtraction(
        provider_key=provider_key,
        claims=(
            PageClaim(
                "https://a.example.com/",
                ServiceClaim(Service.PHYSIOTHERAPY, quote, 0.9),
            ),
            PageClaim(
                "https://a.example.com/services",
                ServiceClaim(Service.SUPPORT_COORDINATION, quote, 0.7),
            ),
        ),
        attributes=PageAttributes(
            age_groups=(AgeGroup.ADULTS,), telehealth=True, home_visits=None, languages=("English",)
        ),
        extracted_at=when,
        input_tokens=1200,
        output_tokens=340,
    )


def test_store_round_trips_the_profile(db_path, tmp_path: Path) -> None:
    store = CachedExtractionStore(create_sqlite_engine(db_path), tmp_path / "extraction")
    profile = make_profile("10000000001", datetime.now(UTC))
    store.save(profile)
    assert store.cached_provider_keys() == {"10000000001"}
    loaded = store.load("10000000001")
    assert loaded == profile


def test_store_projection_has_no_quotes(db_path, tmp_path: Path) -> None:
    store = CachedExtractionStore(create_sqlite_engine(db_path), tmp_path / "extraction")
    store.save(make_profile("10000000001", datetime.now(UTC)))
    with Session(create_sqlite_engine(db_path)) as session:
        rows = session.scalars(select(ServiceClaimRow)).all()
        assert sorted((r.service, round(r.confidence, 1)) for r in rows) == [
            ("physiotherapy", 0.9),
            ("support_coordination", 0.7),
        ]
        assert all("physiotherapy and exercise" not in str(r) for r in rows)
        attribute = session.get(ProviderAttributeRow, "10000000001")
        assert attribute is not None
        assert attribute.telehealth is True
        assert attribute.home_visits is None
        assert json.loads(attribute.age_groups) == ["adults"]


def test_store_save_is_idempotent_per_provider(db_path, tmp_path: Path) -> None:
    store = CachedExtractionStore(create_sqlite_engine(db_path), tmp_path / "extraction")
    when = datetime.now(UTC)
    store.save(make_profile("10000000001", when))
    store.save(make_profile("10000000001", when))
    with Session(create_sqlite_engine(db_path)) as session:
        assert len(session.execute(select(ServiceClaimRow)).all()) == 2


def test_store_rejects_corrupt_cache(tmp_path: Path, db_path) -> None:
    store = CachedExtractionStore(create_sqlite_engine(db_path), tmp_path / "extraction")
    (tmp_path / "extraction" / "broken.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ExtractionCacheError):
        store.load("broken")


def test_pages_source_reads_ok_text_only(db_path, tmp_path: Path) -> None:
    pages_dir = tmp_path / "pages"
    engine = create_sqlite_engine(db_path)
    text = "Alpha provides physiotherapy for NDIS participants."
    store = FilePageStore(engine, pages_dir)
    store.save(
        PageMeta(
            url="https://a.example.com/",
            provider_key="10000000001",
            fetched_at=datetime.now(UTC),
            status=FetchStatus.OK,
            source=PageSource.LIVE,
            text_hash=text_hash(text),
            text_path=text_path("https://a.example.com/"),
            title="Alpha",
        ),
        text,
    )
    store.save(
        PageMeta(
            url="https://a.example.com/x",
            provider_key="10000000001",
            fetched_at=datetime.now(UTC),
            status=FetchStatus.EMPTY,
            source=PageSource.LIVE,
        ),
        None,
    )
    source = FilePageTextSource(engine, pages_dir)
    pages = source.ok_pages()
    assert len(pages) == 1
    assert pages[0].provider_key == "10000000001"
    assert pages[0].text == text
    assert pages[0].title == "Alpha"


def test_pages_source_survives_a_missing_text_file(db_path, tmp_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    FilePageStore(engine, tmp_path / "pages").save(
        PageMeta(
            url="https://a.example.com/",
            provider_key="k",
            fetched_at=datetime.now(UTC),
            status=FetchStatus.OK,
            source=PageSource.LIVE,
            text_hash="x",
            text_path="missing.txt",
            title=None,
        ),
        None,
    )
    pages = FilePageTextSource(engine, tmp_path / "pages").ok_pages()
    assert len(pages) == 1 and pages[0].text == ""


def test_regex_baseline_reads_slugs_per_place(tmp_path: Path) -> None:
    path = write_maps_export(tmp_path / "maps.db", MAPS_ROWS, MAPS_PAGES)
    connection = sqlite3.connect(path)
    with connection:
        connection.executescript(
            """
            create table services (id integer primary key, slug text not null);
            create table provider_services (
                place_id text not null, service_id integer not null,
                matches integer not null, evidence text, source_url text,
                detected_at text not null, primary key (place_id, service_id));
            insert into services values (14, 'occupational_therapy'), (15, 'physiotherapy');
            insert into provider_services values
                ('place-alpha', 14, 3, 'e', 'u', 'now'),
                ('place-alpha', 15, 1, 'e', 'u', 'now');
            """
        )
    connection.close()
    baseline = SqliteRegexBaseline(path).baseline()
    assert baseline == {"place-alpha": frozenset({"occupational_therapy", "physiotherapy"})}


def test_regex_baseline_on_export_without_tables(tmp_path: Path) -> None:
    with pytest.raises(MapsExportError):
        SqliteRegexBaseline(tmp_path / "none.db").baseline()
