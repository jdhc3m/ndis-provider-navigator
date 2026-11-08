"""GET /providers against a temp SQLite database seeded through the repository."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository
from ndis.api import app as api_app
from ndis.api.app import create_app, list_providers
from ndis.api.dependencies import (
    DB_PATH_ENV,
    DEFAULT_DB_PATH,
    database_path,
    list_providers_use_case,
    provider_repository,
    provider_repository_for,
)
from ndis.api.schemas import ProviderOut
from ndis.application.providers import ListProviders
from tests.conftest import SEED_PROVIDERS


@pytest.fixture
def client(
    seeded_repository: SqlAlchemyProviderRepository, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    with TestClient(create_app()) as client:
        yield client


def test_database_path_comes_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    assert database_path() == DEFAULT_DB_PATH
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "x.db"))
    assert database_path() == tmp_path / "x.db"


def test_dependencies_build_the_use_case(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, seeded_repository: SqlAlchemyProviderRepository
) -> None:
    monkeypatch.setenv(DB_PATH_ENV, str(db_path))
    repository = provider_repository()
    assert repository is provider_repository_for(db_path.resolve())
    assert isinstance(list_providers_use_case(repository), ListProviders)
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
    [{"limit": 0}, {"limit": 1000}, {"offset": -1}, {"postcode": "abc"}, {"brisbane_area": "x"}],
)
def test_list_providers_rejects_bad_parameters(client: TestClient, params: dict[str, str]) -> None:
    assert client.get("/providers", params=params).status_code == 422


def test_endpoint_is_the_registered_handler() -> None:
    names = {getattr(route, "endpoint", None).__name__ for route in create_app().routes}
    assert list_providers.__name__ in names
