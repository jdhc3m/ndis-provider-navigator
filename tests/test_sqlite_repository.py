"""SQLAlchemy SQLite repositories: filters, pagination, exact money round-trip, full refresh,
the links-to-register website join and the private-text/public-metadata page store."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from ndis.adapters.sqlite_repository import (
    FilePageStore,
    SqlAlchemyProviderLinkRepository,
    SqlAlchemyProviderRepository,
    SqlAlchemyProviderSiteSource,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.providers import ProviderQuery
from ndis.domain.entity_resolution import (
    Candidate,
    EntityMatch,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
)
from ndis.domain.pages import FetchStatus, PageMeta, PageSource, ProviderSite
from tests.conftest import SEED_OUTLETS, SEED_PROVIDERS, SEED_SUPPORT_ITEMS, make_place


def abns(repository: SqlAlchemyProviderRepository, query: ProviderQuery) -> list[str]:
    return [provider.abn for provider in repository.find(query).items]


def test_tables_and_indexes_exist(seeded_repository: SqlAlchemyProviderRepository) -> None:
    inspector = inspect(seeded_repository._engine)
    assert set(inspector.get_table_names()) == {
        "providers",
        "outlets",
        "provider_registration_groups",
        "support_items",
        "provider_links",
        "page_meta",
    }
    assert {index["name"] for index in inspector.get_indexes("outlets")} >= {
        "ix_outlets_postcode",
        "ix_outlets_state",
    }


def test_round_trip_preserves_entities(seeded_repository: SqlAlchemyProviderRepository) -> None:
    page = seeded_repository.find(ProviderQuery(limit=200))
    assert page.total == len(SEED_PROVIDERS)
    assert sorted(page.items, key=lambda p: p.abn) == sorted(SEED_PROVIDERS, key=lambda p: p.abn)
    assert [p.business_name for p in page.items] == sorted(p.business_name for p in SEED_PROVIDERS)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (ProviderQuery(state="QLD"), ["10000000001", "10000000002", "10000000004", "10000000005"]),
        (ProviderQuery(state="NSW"), ["10000000003"]),
        (ProviderQuery(state="VIC"), ["10000000005"]),
        (ProviderQuery(postcode="4101"), ["10000000001", "10000000004"]),
        (ProviderQuery(postcode="4000"), ["10000000002", "10000000005"]),
        (ProviderQuery(group="Plan Management"), ["10000000002", "10000000005"]),
        (ProviderQuery(group="Therapeutic Supports", status="Revoked"), []),
        (ProviderQuery(status="Revoked"), ["10000000004"]),
        (ProviderQuery(brisbane_area=False), ["10000000003"]),
        (ProviderQuery(brisbane_area=True, state="VIC"), ["10000000005"]),
    ],
)
def test_filters(
    seeded_repository: SqlAlchemyProviderRepository, query: ProviderQuery, expected: list[str]
) -> None:
    page = seeded_repository.find(query)
    assert page.total == len(expected)
    assert sorted(p.abn for p in page.items) == expected


def test_pagination(seeded_repository: SqlAlchemyProviderRepository) -> None:
    first = seeded_repository.find(ProviderQuery(limit=2, offset=0))
    second = seeded_repository.find(ProviderQuery(limit=2, offset=2))
    last = seeded_repository.find(ProviderQuery(limit=2, offset=4))
    assert (first.total, second.total, last.total) == (5, 5, 5)
    assert [p.abn for p in first.items] == ["10000000001", "10000000002"]
    assert [p.abn for p in second.items] == ["10000000004", "10000000005"]
    assert [p.abn for p in last.items] == ["10000000003"]
    assert (last.limit, last.offset) == (2, 4)
    assert seeded_repository.find(ProviderQuery(limit=2, offset=40)).items == ()


def test_replace_all_is_a_full_refresh(db_path: Path) -> None:
    repository = SqlAlchemyProviderRepository(create_sqlite_engine(db_path))
    repository.replace_all(SEED_PROVIDERS, SEED_OUTLETS)
    repository.replace_all(SEED_PROVIDERS[:1], SEED_OUTLETS[:1])
    assert abns(repository, ProviderQuery()) == ["10000000001"]
    assert abns(repository, ProviderQuery(postcode="4000")) == []
    reopened = SqlAlchemyProviderRepository(create_sqlite_engine(db_path))
    assert reopened.find(ProviderQuery()).total == 1


def test_candidate_lookups(seeded_repository: SqlAlchemyProviderRepository) -> None:
    alpha, beta, gamma, delta, epsilon = SEED_PROVIDERS
    assert seeded_repository.find_by_website_domain("10000000001.example.com.au") == (alpha,)
    assert seeded_repository.find_by_website_domain("www.10000000001.example.com.au") == ()
    # Business and legal name (``ALPHA THERAPY``) normalise to the same key; one hit, not two.
    assert seeded_repository.find_by_normalised_name("alpha therapy") == (alpha,)
    assert seeded_repository.find_by_normalised_name("Alpha Therapy") == ()
    assert seeded_repository.find_by_normalised_name("") == ()
    assert seeded_repository.brisbane_providers() == (alpha, beta, delta, epsilon)
    assert gamma not in seeded_repository.brisbane_providers()


def test_name_index_is_rebuilt_after_replace_all(
    seeded_repository: SqlAlchemyProviderRepository,
) -> None:
    assert seeded_repository.find_by_normalised_name("gamma care") != ()
    seeded_repository.replace_all(SEED_PROVIDERS[:1], SEED_OUTLETS[:1])
    assert seeded_repository.find_by_normalised_name("gamma care") == ()
    assert seeded_repository.find_by_normalised_name("alpha therapy") == (SEED_PROVIDERS[0],)


def test_provider_links_round_trip(db_path: Path) -> None:
    engine = create_sqlite_engine(db_path)
    repository = SqlAlchemyProviderLinkRepository(engine)
    alpha = SEED_PROVIDERS[0]
    linked = ProviderLink(
        make_place("place-b", "Alpha Therapy", website="https://www.alpha.example/x"),
        EntityMatch("place-b", alpha.abn, MatchStatus.REGISTERED, MatchStrategy.DOMAIN, 0.95, "A"),
    )
    unmatched = ProviderLink(
        make_place("place-a", "Nobody", category=None, postcode=None),
        EntityMatch("place-a", None, MatchStatus.UNREGISTERED, MatchStrategy.NONE, 0.4, None),
    )
    ambiguous = ProviderLink(
        make_place("place-c", "Franchise", website="https://franchise.example"),
        EntityMatch(
            "place-c", None, MatchStatus.AMBIGUOUS, MatchStrategy.DOMAIN, 0.3, None,
            (Candidate("10000000001", "Alpha Therapy"), Candidate("10000000002", "Beta Supports")),
        ),
    )  # fmt: skip
    repository.replace_all([linked, unmatched, ambiguous])
    assert repository.all() == (unmatched, linked, ambiguous)
    repository.replace_all([linked])
    assert SqlAlchemyProviderLinkRepository(create_sqlite_engine(db_path)).all() == (linked,)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT abn, match_status, strategy, latitude, longitude, candidates "
                "FROM provider_links"
            )
        ).one()
    assert tuple(row) == (alpha.abn, "registered", "domain", -27.48, 153.01, "[]")


def test_support_items_round_trip_exact_decimals(
    seeded_support_items: SqlAlchemySupportItemRepository,
) -> None:
    for seed in SEED_SUPPORT_ITEMS:
        assert seeded_support_items.get(seed.item_number) == seed
    stored = seeded_support_items.get("01_002_0107_1_1")
    assert stored is not None
    assert isinstance(stored.limits.national, Decimal)
    assert str(stored.limits.remote) == "115.60"
    assert seeded_support_items.get("does_not_exist") is None
    assert seeded_support_items.get("01_002_0107_1_1 ") is None


def test_support_item_prices_are_stored_as_text(
    seeded_support_items: SqlAlchemySupportItemRepository,
) -> None:
    with seeded_support_items._engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT item_number, national, typeof(national), remote, very_remote, end_date "
                "FROM support_items ORDER BY item_number"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("01_002_0107_1_1", "82.57", "text", "115.60", "123.86", None),
        ("01_003_0107_1_1", None, "null", None, None, None),
        ("01_045_0115_1_1", "708.75", "text", "992.25", "1063.13", "2027-06-30"),
    ]


def test_support_items_share_the_register_database(
    seeded_repository: SqlAlchemyProviderRepository, db_path: Path
) -> None:
    catalogue = SqlAlchemySupportItemRepository(seeded_repository._engine)
    catalogue.replace_all(SEED_SUPPORT_ITEMS)
    catalogue.replace_all(SEED_SUPPORT_ITEMS[:1])
    assert catalogue.get("01_045_0115_1_1") is None
    reopened = SqlAlchemySupportItemRepository(create_sqlite_engine(db_path))
    assert reopened.get("01_002_0107_1_1") == SEED_SUPPORT_ITEMS[0]
    assert seeded_repository.find(ProviderQuery()).total == len(SEED_PROVIDERS)


def test_provider_sites_join_links_to_the_register_website(
    seeded_repository: SqlAlchemyProviderRepository, db_path: Path
) -> None:
    engine = create_sqlite_engine(db_path)
    alpha, beta = SEED_PROVIDERS[0], SEED_PROVIDERS[1]
    SqlAlchemyProviderLinkRepository(engine).replace_all(
        [
            ProviderLink(
                make_place("place-b", "Alpha", website="https://alpha.example/?utm_source=gbp"),
                EntityMatch("place-b", alpha.abn, MatchStatus.REGISTERED, MatchStrategy.DOMAIN,
                            0.95, "Alpha Therapy"),
            ),
            ProviderLink(
                make_place("place-a", "Nobody", website="https://nobody.example"),
                EntityMatch("place-a", None, MatchStatus.UNREGISTERED, MatchStrategy.NONE, 0.6,
                            None),
            ),
            ProviderLink(
                make_place("place-c", "Beta Supports"),
                EntityMatch("place-c", beta.abn, MatchStatus.REGISTERED, MatchStrategy.NAME, 0.9,
                            "Beta Supports"),
            ),
            ProviderLink(
                make_place("place-d", "Hire Co", category="Recruitment Agency"),
                EntityMatch("place-d", None, MatchStatus.NOT_A_PROVIDER, MatchStrategy.CATEGORY,
                            0.9, None),
            ),
        ]
    )  # fmt: skip
    assert SqlAlchemyProviderSiteSource(engine).sites() == (
        ProviderSite("place-a", None, MatchStatus.UNREGISTERED, None, "https://nobody.example"),
        ProviderSite("place-b", alpha.abn, MatchStatus.REGISTERED, alpha.website,
                     "https://alpha.example/?utm_source=gbp"),
        ProviderSite("place-c", beta.abn, MatchStatus.REGISTERED, beta.website, None),
        ProviderSite("place-d", None, MatchStatus.NOT_A_PROVIDER, None, None),
    )  # fmt: skip


def test_page_store_writes_text_privately_and_metadata_publicly(
    db_path: Path, tmp_path: Path
) -> None:
    engine = create_sqlite_engine(db_path)
    pages_dir = tmp_path / "data" / "private" / "pages"
    store = FilePageStore(engine, pages_dir)
    fetched_at = datetime(2026, 11, 7, 3, 49, 42, tzinfo=UTC)
    ok = PageMeta(
        url="https://alpha.example/",
        provider_key="10000000001",
        fetched_at=fetched_at,
        status=FetchStatus.OK,
        source=PageSource.MAPS_CRAWL,
        text_hash="ab" * 32,
        text_path="0123456789abcdef.txt",
        title="Alpha",
    )
    failed = PageMeta("https://beta.example/", "place-beta", fetched_at, FetchStatus.TIMEOUT,
                      PageSource.LIVE)  # fmt: skip
    store.save(ok, "Alpha Therapy\nWest End")
    store.save(failed, None)
    assert (pages_dir / "0123456789abcdef.txt").read_text(encoding="utf-8") == (
        "Alpha Therapy\nWest End"
    )
    assert sorted(path.name for path in pages_dir.iterdir()) == ["0123456789abcdef.txt"]
    assert store.all() == (ok, failed)
    assert store.providers_with_ok_pages() == frozenset({"10000000001"})
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(page_meta)"))}
        rows = connection.execute(
            text("SELECT url, fetched_at, status, text_hash FROM page_meta ORDER BY url")
        ).all()
    assert columns >= {"url", "fetched_at", "status", "text_hash"}
    assert "text" not in columns
    assert [tuple(row) for row in rows] == [
        ("https://alpha.example/", "2026-11-07T03:49:42+00:00", "ok", "ab" * 32),
        ("https://beta.example/", "2026-11-07T03:49:42+00:00", "timeout", None),
    ]


def test_page_store_upserts_by_provider_and_url(db_path: Path, tmp_path: Path) -> None:
    store = FilePageStore(create_sqlite_engine(db_path), tmp_path / "pages")
    when = datetime(2026, 11, 9, tzinfo=UTC)
    first = PageMeta("https://alpha.example/", "k", when, FetchStatus.TIMEOUT, PageSource.LIVE)
    store.save(first, None)
    store.save(first, None)
    later = PageMeta("https://alpha.example/", "k", when, FetchStatus.OK, PageSource.LIVE,
                     "cd" * 32, "f.txt", None)  # fmt: skip
    store.save(later, "text")
    other = PageMeta("https://alpha.example/", "other", when, FetchStatus.EMPTY, PageSource.LIVE)
    store.save(other, None)
    assert store.all() == (later, other)
    assert store.providers_with_ok_pages() == frozenset({"k"})
    assert store.stored_pages() == frozenset(
        {("k", "https://alpha.example/"), ("other", "https://alpha.example/")}
    )
