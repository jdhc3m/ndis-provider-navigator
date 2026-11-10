"""Postcode CSV loader: one centroid per postcode, rejection counts and the parse-ratio gate."""

import logging
from pathlib import Path

import pytest

from ndis.adapters.postcodes_csv import (
    PostcodesFormatError,
    iter_postcode_rows,
    load_postcodes,
    parse_postcodes,
)
from ndis.adapters.sqlite_repository import SqlAlchemyPostcodeCentroids, create_sqlite_engine
from scripts.load_postcodes import main as load_postcodes_main
from tests.conftest import POSTCODE_ROWS, write_postcodes


def test_iter_postcode_rows_streams_and_strips_bom(postcodes_csv: Path) -> None:
    rows = iter_postcode_rows(postcodes_csv)
    first = next(rows)
    assert (first["postcode"], first["locality"]) == ("4000", "BRISBANE")
    assert sum(1 for _ in rows) == len(POSTCODE_ROWS) - 1


def test_iter_postcode_rows_rejects_wrong_header(tmp_path: Path) -> None:
    other = tmp_path / "other.csv"
    other.write_text("postcode,suburb\n4000,BRISBANE\n", encoding="utf-8")
    with pytest.raises(PostcodesFormatError, match="missing columns"):
        list(iter_postcode_rows(other))


def test_parse_postcodes_averages_localities_and_counts_rejections(postcodes_csv: Path) -> None:
    centroids, report = parse_postcodes(postcodes_csv)
    assert report.input_rows == 11
    assert report.parsed_rows == 10
    assert dict(report.rejection_reasons) == {"missing_coordinates": 1}
    by_postcode = {centroid.postcode: centroid for centroid in centroids}
    # 4001 (PO boxes only, no location) has no centroid; the others have one each.
    assert sorted(by_postcode) == ["2000", "2150", "3000", "4000", "4101", "4350", "4870"]
    brisbane = by_postcode["4000"]
    assert brisbane.locality_count == 2
    assert brisbane.coordinates.latitude == pytest.approx((-27.4679 + -27.4689) / 2)
    assert brisbane.coordinates.longitude == pytest.approx((153.0281 + 153.0178) / 2)
    assert by_postcode["4870"].locality_count == 1
    assert "postcodes_persisted=0" in report.summary()
    assert "missing_coordinates=1" in report.summary()


def test_load_postcodes_persists_and_replaces(
    postcodes_csv: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    with caplog.at_level(logging.INFO):
        report = load_postcodes(postcodes_csv, repository)
    assert report.postcodes_persisted == 7
    assert "postcode load: input_rows=11" in caplog.text
    found = repository.coordinates_for(["4101", "4870", "4001", "9999"])
    assert sorted(found) == ["4101", "4870"]
    assert found["4101"].latitude == pytest.approx(-27.484254)
    assert repository.coordinates_for([]) == {}
    # A second load replaces, never appends.
    smaller = write_postcodes(postcodes_csv, POSTCODE_ROWS[:2])
    load_postcodes(smaller, repository)
    assert sorted(repository.coordinates_for(["4000", "4101", "4870"])) == ["4000"]


def test_load_postcodes_refuses_low_parse_ratio(tmp_path: Path, db_path: Path) -> None:
    mostly_bad = write_postcodes(
        tmp_path / "bad.csv",
        [POSTCODE_ROWS[0], *["9,4002,BOXES,QLD,0,0" + "," * 44] * 3],
    )
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    with pytest.raises(PostcodesFormatError, match="25.0%"):
        load_postcodes(mostly_bad, repository)
    assert repository.coordinates_for(["4000"]) == {}


def test_load_script_loads_and_reports_missing_file(
    postcodes_csv: Path, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert load_postcodes_main(["--csv", str(postcodes_csv), "--db", str(db_path)]) == 0
    assert "postcodes_persisted=7" in caplog.text
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    assert sorted(repository.coordinates_for(["4000", "4350"])) == ["4000", "4350"]
    assert load_postcodes_main(["--csv", str(tmp_path / "nope.csv"), "--db", str(db_path)]) == 1
    assert "not found" in caplog.text
    junk = tmp_path / "junk.csv"
    junk.write_text("a,b\n1,2\n", encoding="utf-8")
    assert load_postcodes_main(["--csv", str(junk), "--db", str(db_path)]) == 1
    assert "postcode load failed" in caplog.text
