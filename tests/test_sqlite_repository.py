"""SQLAlchemy SQLite repositories: filters, pagination, exact money round-trip, full refresh."""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.providers import ProviderQuery
from tests.conftest import SEED_OUTLETS, SEED_PROVIDERS, SEED_SUPPORT_ITEMS


def abns(repository: SqlAlchemyProviderRepository, query: ProviderQuery) -> list[str]:
    return [provider.abn for provider in repository.find(query).items]


def test_tables_and_indexes_exist(seeded_repository: SqlAlchemyProviderRepository) -> None:
    inspector = inspect(seeded_repository._engine)
    assert set(inspector.get_table_names()) == {
        "providers",
        "outlets",
        "provider_registration_groups",
        "support_items",
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
