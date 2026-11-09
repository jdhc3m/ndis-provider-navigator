"""The resolve_entities script end to end on a seeded register and a synthetic Maps export."""

import logging
from pathlib import Path

import pytest

from ndis.adapters.er_sample import read_sample
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderLinkRepository,
    SqlAlchemyProviderRepository,
    create_sqlite_engine,
)
from ndis.domain.entity_resolution import MatchStatus
from scripts.resolve_entities import main


def run(tmp_path: Path, maps: Path, db: Path) -> tuple[int, Path, Path]:
    sample, report = tmp_path / "evals" / "er_sample.yaml", tmp_path / "reports" / "er.md"
    code = main(
        ["--maps", str(maps), "--db", str(db), "--sample", str(sample), "--report", str(report)]
    )
    return code, sample, report


def test_script_writes_links_sample_and_report(
    tmp_path: Path,
    maps_export: Path,
    seeded_repository: SqlAlchemyProviderRepository,
    db_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="resolve_entities"):
        code, sample, report = run(tmp_path, maps_export, db_path)
    assert code == 0
    links = SqlAlchemyProviderLinkRepository(create_sqlite_engine(db_path)).all()
    statuses = {link.place.place_id: link.match.match_status for link in links}
    assert statuses == {
        "place-alpha": MatchStatus.REGISTERED,
        "place-beta": MatchStatus.REGISTERED,
        "place-delta": MatchStatus.REVOKED_OR_BANNED,
        "place-recruit": MatchStatus.NOT_A_PROVIDER,
        "place-unknown": MatchStatus.UNREGISTERED,
    }
    assert "resolved 5 places; status: ambiguous=0" in caplog.text
    rows = read_sample(sample)
    assert len(rows) == 5 and all(row.verdict is None for row in rows)
    assert {row.match_status for row in rows} == {s.value for s in statuses.values()}
    text = report.read_text(encoding="utf-8")
    assert "| registered | 2 | 40.0% |" in text
    assert "| domain | 2 |" in text
    assert "| not_a_provider | 40 | 1 | -39 |" in text


def test_rerun_keeps_filled_verdicts(
    tmp_path: Path,
    maps_export: Path,
    seeded_repository: SqlAlchemyProviderRepository,
    db_path: Path,
) -> None:
    _, sample, _ = run(tmp_path, maps_export, db_path)
    edited = sample.read_text(encoding="utf-8").replace("verdict: null", "verdict: correct", 1)
    sample.write_text(edited, encoding="utf-8")
    first = read_sample(sample)
    assert run(tmp_path, maps_export, db_path)[0] == 0
    assert read_sample(sample) == first


def test_missing_inputs_stop_with_a_message(
    tmp_path: Path, maps_export: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="resolve_entities"):
        assert run(tmp_path, tmp_path / "nope.db", db_path)[0] == 1
        assert "Maps export not found" in caplog.text
        assert run(tmp_path, maps_export, tmp_path / "no-register.db")[0] == 1
        assert "register database not found" in caplog.text
    assert not (tmp_path / "evals").exists()
