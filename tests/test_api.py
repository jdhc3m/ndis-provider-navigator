"""GET /providers and GET /price-limits against a temp SQLite database seeded via the repos."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
)
from ndis.api import app as api_app
from ndis.api.app import create_app, get_price_limit, list_providers
from ndis.api.dependencies import (
    DB_PATH_ENV,
    DEFAULT_DB_PATH,
    database_path,
    engine_for,
    get_price_limit_use_case,
    list_providers_use_case,
    provider_repository,
    provider_repository_for,
    support_item_repository,
    support_item_repository_for,
)
from ndis.api.schemas import PriceLimitOut, ProviderOut
from ndis.application.catalogue import GetPriceLimit, PriceLimitResult
from ndis.application.providers import ListProviders
from ndis.domain.entities import Remoteness
from tests.conftest import SEED_PROVIDERS, SEED_SUPPORT_ITEMS


@pytest.fixture
def client(
    seeded_repository: SqlAlchemyProviderRepository,
    seeded_support_items: SqlAlchemySupportItemRepository,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    with TestClient(create_app()) as client:
        yield client


def test_database_path_comes_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    assert database_path() == DEFAULT_DB_PATH
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "x.db"))
    assert database_path() == tmp_path / "x.db"


def test_dependencies_build_the_use_cases(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, seeded_repository: SqlAlchemyProviderRepository
) -> None:
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    repository = provider_repository()
    assert repository is provider_repository_for(db_path.resolve())
    assert isinstance(list_providers_use_case(repository), ListProviders)
    catalogue = support_item_repository()
    assert catalogue is support_item_repository_for(db_path.resolve())
    assert isinstance(get_price_limit_use_case(catalogue), GetPriceLimit)
    # Both repositories share one engine per database file.
    assert repository._engine is catalogue._engine is engine_for(db_path.resolve())
    assert api_app.app.title == "NDIS Provider Navigator"


def test_list_providers_default_page(client: TestClient) -> None:
    response = client.get("/providers")
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (5, 20, 0)
    assert [item["business_name"] for item in body["items"]] == sorted(
        p.business_name for p in SEED_PROVIDERS
    )
    alpha = body["items"][0]
    assert alpha == ProviderOut.from_provider(SEED_PROVIDERS[0]).model_dump(mode="json")
    assert alpha["head_office"] == {"suburb": "SOMEWHERE", "state": "QLD", "postcode": "4101"}
    assert alpha["registration_groups"] == ["Therapeutic Supports"]
    assert alpha["registration_expires_on"] == "2028-01-01"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"state": "nsw"}, ["10000000003"]),
        ({"postcode": "4000"}, ["10000000002", "10000000005"]),
        ({"group": "Plan Management"}, ["10000000002", "10000000005"]),
        ({"status": "Revoked"}, ["10000000004"]),
        ({"status": "approved", "state": "NSW"}, ["10000000003"]),
        ({"brisbane_area": "false"}, ["10000000003"]),
        ({"state": "QLD", "status": "Approved", "group": "Therapeutic Supports"},
         ["10000000001", "10000000005"]),
    ],
)  # fmt: skip
def test_list_providers_filters(
    client: TestClient, params: dict[str, str], expected: list[str]
) -> None:
    body = client.get("/providers", params=params).json()
    assert body["total"] == len(expected)
    assert sorted(item["abn"] for item in body["items"]) == expected


def test_list_providers_pagination(client: TestClient) -> None:
    page = client.get("/providers", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5
    assert (page["limit"], page["offset"]) == (2, 2)
    assert [item["abn"] for item in page["items"]] == ["10000000004", "10000000005"]


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 1000},
        {"offset": -1},
        {"postcode": "abc"},
        {"brisbane_area": "x"},
        {"status": "Pending"},
    ],
)
def test_list_providers_rejects_bad_parameters(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/providers", params=params).status_code == 422


def test_get_price_limit_national_default(client: TestClient) -> None:
    response = client.get("/price-limits", params={"item": "01_002_0107_1_1", "state": "QLD"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "item_number": "01_002_0107_1_1",
        "name": "Assistance With Self-Care Activities - Weekday Night",
        "registration_group_number": "0107",
        "registration_group_name": "Daily Personal Activities",
        "unit": "H",
        "quotable": False,
        "legacy": False,
        "start_date": "2026-07-01",
        "end_date": None,
        "state": "QLD",
        "remoteness": "national",
        "price_limit": "82.57",
        "limits": {"national": "82.57", "remote": "115.60", "very_remote": "123.86"},
        "source": "NDIS Support Catalogue 2026-27",
    }
    result = PriceLimitResult(
        item=SEED_SUPPORT_ITEMS[0],
        state="QLD",
        remoteness=Remoteness.NATIONAL,
        limit=Decimal("82.57"),
    )
    assert body == PriceLimitOut.from_result(result).model_dump(mode="json")


@pytest.mark.parametrize(
    ("params", "remoteness", "limit"),
    [
        ({"item": "01_002_0107_1_1", "state": "nt", "remoteness": "remote"}, "remote", "115.60"),
        ({"item": "01_002_0107_1_1", "state": "WA", "remoteness": "Very_Remote"},
         "very_remote", "123.86"),
        ({"item": " 01_002_0107_1_1 ", "state": "nsw", "remoteness": ""}, "national", "82.57"),
        ({"item": "01_045_0115_1_1", "state": "QLD", "remoteness": "national"},
         "national", "708.75"),
    ],
)  # fmt: skip
def test_get_price_limit_remoteness_and_normalisation(
    client: TestClient, params: dict[str, str], remoteness: str, limit: str
) -> None:
    body = client.get("/price-limits", params=params).json()
    assert (body["state"], body["remoteness"], body["price_limit"]) == (
        params["state"].strip().upper(),
        remoteness,
        limit,
    )


def test_get_price_limit_legacy_item_reports_end_date(client: TestClient) -> None:
    body = client.get("/price-limits", params={"item": "01_045_0115_1_1", "state": "QLD"}).json()
    assert (body["legacy"], body["end_date"], body["unit"]) == (True, "2027-06-30", "H")


def test_get_price_limit_quotable_item_has_null_limit(client: TestClient) -> None:
    response = client.get("/price-limits", params={"item": "01_003_0107_1_1", "state": "VIC"})
    assert response.status_code == 200
    body = response.json()
    assert body["quotable"] is True
    assert body["price_limit"] is None
    assert body["limits"] == {"national": None, "remote": None, "very_remote": None}


def test_get_price_limit_unknown_item_is_404(client: TestClient) -> None:
    response = client.get("/price-limits", params={"item": "99_999_0000_0_0", "state": "QLD"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        "support item '99_999_0000_0_0' is not in the 2026-27 support catalogue"
    )


@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"state": "QLD"}, "item"),
        ({"item": "01_002_0107_1_1"}, "state"),
        ({"item": "01_002_0107_1_1", "state": "NZ"}, "state must be one of"),
        ({"item": "01_002_0107_1_1", "state": ""}, "state must be one of"),
        ({"item": "  ", "state": "QLD"}, "item must not be blank"),
        ({"item": "01_002_0107_1_1", "state": "QLD", "remoteness": "rural"},
         "remoteness must be one of"),
    ],
)  # fmt: skip
def test_get_price_limit_rejects_bad_parameters(
    client: TestClient, params: dict[str, str], fragment: str
) -> None:
    response = client.get("/price-limits", params=params)
    assert response.status_code == 422
    assert fragment in str(response.json()["detail"])


def test_endpoints_are_the_registered_handlers() -> None:
    names = {getattr(route, "endpoint", None).__name__ for route in create_app().routes}
    assert {list_providers.__name__, get_price_limit.__name__} <= names
