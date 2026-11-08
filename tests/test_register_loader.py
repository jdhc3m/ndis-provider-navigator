"""Streaming register loader: aggregation, counts and the parse-ratio gate."""

import logging
from collections.abc import Iterable
from pathlib import Path

import pytest

from ndis.adapters.register_csv import (
    RegisterFormatError,
    count_distinct_abns,
    iter_register_rows,
    load_register,
    parse_register,
)
from ndis.adapters.sqlite_repository import SqlAlchemyProviderRepository, create_sqlite_engine
from ndis.application.providers import ProviderQuery
from ndis.domain.entities import Outlet, Provider
from scripts.load_register import main as load_register_main


def test_iter_register_rows_streams_and_strips_bom(register_csv: Path) -> None:
    rows = iter_register_rows(register_csv)
    first = next(rows)
    assert first["Provider business name"] == "HUNTER CARERS LTD"
    assert sum(1 for _ in rows) == 5
    assert count_distinct_abns(register_csv) == 5


def test_iter_register_rows_rejects_wrong_header(tmp_path: Path) -> None:
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(RegisterFormatError):
        list(iter_register_rows(other))


def test_parse_register_aggregates_providers_and_reports_counts(
    register_csv_with_bad_rows: Path,
) -> None:
    providers, outlets, report = parse_register(register_csv_with_bad_rows)
    assert report.input_rows == 8
    assert report.parsed_rows == 6
    assert report.rejected_rows == 2
    assert dict(report.rejection_reasons) == {"invalid_abn": 1, "invalid_expiry_date": 1}
    assert report.brisbane_rows == 3
    assert report.brisbane_providers == 3
    assert len(providers) == 5
    assert len(outlets) == 6
    # The Parramatta provider is flagged because its second row has a Brisbane outlet.
    assert providers["99888777666"].brisbane_area is True
    assert providers["99888777666"].registration_groups == ()
    assert "parsed_rows=6" in report.summary() and "invalid_abn=1" in report.summary()


def test_load_register_refuses_low_parse_ratio(
    register_csv_with_bad_rows: Path, db_path: Path
) -> None:
    repository = SqlAlchemyProviderRepository(create_sqlite_engine(db_path))
    with pytest.raises(RegisterFormatError, match="75.0%"):
        load_register(register_csv_with_bad_rows, repository)
    assert repository.find(ProviderQuery()).total == 0


def test_load_register_persists_and_logs(
    register_csv: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repository = SqlAlchemyProviderRepository(create_sqlite_engine(db_path))
    with caplog.at_level(logging.INFO, logger="ndis.adapters.register_csv"):
        report = load_register(register_csv, repository)
    assert (report.providers_persisted, report.outlets_persisted) == (5, 6)
    assert report.parse_ratio == 1.0
    assert "providers_persisted=5 outlets_persisted=6" in caplog.text
    page = repository.find(ProviderQuery(brisbane_area=True))
    assert sorted(p.abn for p in page.items) == ["11222333444", "55666777888", "99888777666"]
    assert repository.find(ProviderQuery(postcode="4006")).items[0].abn == "99888777666"


class RecordingRepository:
    def __init__(self) -> None:
        self.saved: tuple[int, int] | None = None

    def replace_all(self, providers: Iterable[Provider], outlets: Iterable[Outlet]) -> None:
        self.saved = (len(list(providers)), len(list(outlets)))


def test_loader_goes_through_the_repository_port(register_csv: Path) -> None:
    repository = RecordingRepository()
    load_register(register_csv, repository)  # type: ignore[arg-type]
    assert repository.saved == (5, 6)


def test_load_register_cli(
    register_csv: Path, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert load_register_main(["--csv", str(register_csv), "--db", str(db_path)]) == 0
    assert "outlets_persisted=6" in caplog.text
    assert load_register_main(["--csv", str(tmp_path / "missing.csv"), "--db", str(db_path)]) == 1
    assert "not found" in caplog.text
