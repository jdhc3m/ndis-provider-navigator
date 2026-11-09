"""GET /meta, the CORS allowance for the Vite dev server, the static mount of the built
front end, and the OpenAPI export script the generated client is checked against."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndis.adapters import tracing
from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository
from ndis.api import dependencies
from ndis.api.app import DEV_ORIGINS, create_app, meta
from ndis.api.dependencies import (
    DB_PATH_ENV,
    DEFAULT_DB_PATH,
    DEFAULT_WEB_DIST,
    REGISTER_CSV_ENV,
    REGISTER_CSV_RELATIVE,
    WEB_DIST_ENV,
    register_csv_path,
    register_snapshot_date,
    web_dist_dir,
)
from ndis.domain.catalogue import CATALOGUE_TITLE, FINANCIAL_YEAR
from ndis.domain.pricing import PRICING_DOCUMENT_TITLE
from scripts.export_openapi import export_openapi

ROOT = Path(__file__).resolve().parents[1]
MODEL_ENV = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "EMBED_MODEL", "RULE_CHUNK_STRATEGY")
TRACING_ENV = (
    tracing.PUBLIC_KEY_ENV,
    tracing.SECRET_KEY_ENV,
    tracing.BASE_URL_ENV,
    tracing.HOST_ENV,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No model, tracing or data configuration at all; paths point into ``tmp_path``."""
    for name in MODEL_ENV + TRACING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(REGISTER_CSV_ENV, str(tmp_path / "register.csv"))
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "ndis.db"))
    monkeypatch.setenv(WEB_DIST_ENV, str(tmp_path / "dist"))
    return tmp_path


def test_meta_reports_unconfigured_models_and_missing_data(clean_env: Path) -> None:
    response = TestClient(create_app()).get("/meta")
    assert response.status_code == 200
    assert response.json() == {
        "llm_model": "",
        "embed_model": "",
        "llm_configured": False,
        "register_snapshot_date": None,
        "catalogue_title": CATALOGUE_TITLE,
        "financial_year": FINANCIAL_YEAR,
        "pricing_document": PRICING_DOCUMENT_TITLE,
        "rule_chunk_strategy": "heading",
        "tracing": {"configured": False, "url": None},
    }
    assert "2025-26" in PRICING_DOCUMENT_TITLE  # the document ticket 06 indexed, not a copy


def test_meta_reports_the_configured_models_snapshot_and_tracing(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example/v1/")
    monkeypatch.setenv("LLM_MODEL", "model-from-env")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("EMBED_MODEL", "embed-from-env")
    monkeypatch.setenv("RULE_CHUNK_STRATEGY", "fixed")
    monkeypatch.setenv(tracing.PUBLIC_KEY_ENV, "pk")
    monkeypatch.setenv(tracing.SECRET_KEY_ENV, "sk")
    monkeypatch.setenv(tracing.HOST_ENV, "https://traces.example")
    register = clean_env / "register.csv"
    register.write_text("ABN\n")
    stamp = time.mktime((2026, 11, 3, 12, 0, 0, 0, 0, -1))
    os.utime(register, (stamp, stamp))

    body = TestClient(create_app()).get("/meta").json()
    assert body["llm_model"] == "model-from-env"
    assert body["embed_model"] == "embed-from-env"
    assert body["llm_configured"] is True
    assert body["rule_chunk_strategy"] == "fixed"
    assert body["register_snapshot_date"] == "2026-11-03"
    assert body["tracing"] == {"configured": True, "url": "https://traces.example"}


def test_tracing_url_prefers_base_url_then_host_then_the_default(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert tracing.tracing_url() is None  # off without keys, whatever the host says
    monkeypatch.setenv(tracing.HOST_ENV, "https://traces.example")
    assert tracing.tracing_url() is None
    monkeypatch.setenv(tracing.PUBLIC_KEY_ENV, "pk")
    monkeypatch.setenv(tracing.SECRET_KEY_ENV, "sk")
    assert tracing.tracing_url() == "https://traces.example"
    monkeypatch.setenv(tracing.BASE_URL_ENV, "https://base.example")
    assert tracing.tracing_url() == "https://base.example"
    monkeypatch.delenv(tracing.BASE_URL_ENV)
    monkeypatch.delenv(tracing.HOST_ENV)
    assert tracing.tracing_url() == tracing.DEFAULT_HOST


def test_register_snapshot_comes_from_the_csv_never_the_database_file(
    clean_env: Path,
) -> None:
    """SQLite rewrites the database mtime on every write, so a database alone yields no
    date rather than the time it was last touched."""
    assert register_snapshot_date() is None
    (clean_env / "ndis.db").write_bytes(b"")
    assert register_snapshot_date() is None
    (clean_env / "register.csv").write_text("ABN\n")
    stamp = time.mktime((2026, 10, 30, 9, 0, 0, 0, 0, -1))
    os.utime(clean_env / "register.csv", (stamp, stamp))
    assert register_snapshot_date().isoformat() == "2026-10-30"  # type: ignore[union-attr]


def test_register_csv_defaults_to_public_beside_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(REGISTER_CSV_ENV, raising=False)
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "elsewhere" / "ndis.db"))
    assert register_csv_path() == tmp_path / "elsewhere" / "public" / "register.csv"
    monkeypatch.delenv(DB_PATH_ENV)
    assert register_csv_path() == DEFAULT_DB_PATH.parent / REGISTER_CSV_RELATIVE
    assert register_csv_path() == Path("data/public/register.csv")


def test_web_dist_defaults_under_the_repository_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WEB_DIST_ENV, raising=False)
    assert web_dist_dir() == DEFAULT_WEB_DIST
    assert DEFAULT_WEB_DIST == ROOT / "web" / "dist"  # from __file__, not the cwd
    assert dependencies.REPO_ROOT == ROOT


def test_meta_route_is_registered_by_name() -> None:
    app = create_app()
    names = {route.endpoint.__name__ for route in app.routes if hasattr(route, "endpoint")}
    assert meta.__name__ in names


def test_dev_server_origin_is_allowed_by_cors(clean_env: Path) -> None:
    client = TestClient(create_app())
    for origin in DEV_ORIGINS:
        response = client.options(
            "/ask",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
    denied = client.get("/meta", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_root_is_404_without_a_built_front_end(clean_env: Path) -> None:
    assert TestClient(create_app()).get("/").status_code == 404


def test_built_front_end_is_served_after_the_api_routes(
    clean_env: Path, seeded_repository: SqlAlchemyProviderRepository
) -> None:
    dist = clean_env / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Navigator</title>")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('app')")
    client = TestClient(create_app())
    root = client.get("/")
    assert root.status_code == 200
    assert "<title>Navigator</title>" in root.text
    assert client.get("/assets/app.js").text == "console.log('app')"
    # API routes keep winning over the mount.
    assert client.get("/meta").status_code == 200
    providers = client.get("/providers", params={"state": "NSW"})
    assert providers.status_code == 200
    assert [item["abn"] for item in providers.json()["items"]] == ["10000000003"]


def test_export_openapi_is_deterministic_and_documents_every_route(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    assert export_openapi(first) == 5
    export_openapi(second)
    assert first.read_bytes() == second.read_bytes()
    document = json.loads(first.read_text())
    assert set(document["paths"]) == {"/providers", "/price-limits", "/ask-rule", "/ask", "/meta"}
    assert "StepOut" in document["components"]["schemas"]
    assert "MetaOut" in document["components"]["schemas"]
    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", str(tmp_path / "cli.json")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "wrote 5 paths" in result.stdout
    assert (tmp_path / "cli.json").read_bytes() == first.read_bytes()
