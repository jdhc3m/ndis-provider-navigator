"""Postcode CSV loader: one centroid per postcode, the Delivery-Area-only locality
gazetteer, rejection counts and the parse-ratio gate."""

import csv
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
from ndis.application.providers import LocalityResolver
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
    centroids, _, report = parse_postcodes(postcodes_csv)
    assert report.input_rows == 13
    assert report.parsed_rows == 11
    assert dict(report.rejection_reasons) == {"missing_coordinates": 1}
    assert report.excluded_rows == 1
    assert report.parse_ratio == pytest.approx(11 / 12)
    by_postcode = {centroid.postcode: centroid for centroid in centroids}
    # 4001 (PO boxes only, no location) and 9999 (NORTH POLE, located but not a place) have
    # no centroid; the LVR row's 4002 and the others have one each.
    assert sorted(by_postcode) == ["2000", "2150", "3000", "4000", "4002", "4101", "4350", "4870"]
    brisbane = by_postcode["4000"]
    assert brisbane.locality_count == 2
    assert brisbane.coordinates.latitude == pytest.approx((-27.4679 + -27.4689) / 2)
    assert brisbane.coordinates.longitude == pytest.approx((153.0281 + 153.0178) / 2)
    assert by_postcode["4870"].locality_count == 1
    assert by_postcode["4002"].locality_count == 1  # the LVR row keeps its centroid
    assert "postcodes_persisted=0" in report.summary()
    assert "missing_coordinates=1" in report.summary()
    assert "excluded_rows=1" in report.summary()


def test_parse_postcodes_persists_delivery_area_rows_only(
    postcodes_csv: Path, tmp_path: Path
) -> None:
    """Only Delivery Area rows become localities, and the fixture's row types are what
    the CSV itself carries (read back from the file, not from the code)."""
    with postcodes_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_type: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_type.setdefault(row["type"], []).append(row)
    assert sorted(by_type) == ["Delivery Area", "LVR", "Post Office Boxes"]
    expected = sorted(
        (row["locality"], row["state"], row["postcode"])
        for row in by_type["Delivery Area"]
        if row["postcode"] != "9999"  # NORTH POLE: typed Delivery Area, still not a place
    )
    assert len(expected) == 10

    _, localities, report = parse_postcodes(postcodes_csv)
    assert dict(report.locality_rows) == {
        "Delivery Area": 11,
        "LVR": 1,
        "Post Office Boxes": 1,
    }
    assert sorted((item.name, item.state, item.postcode) for item in localities) == expected
    assert report.localities_persisted == 0  # set by load_postcodes, not parse_postcodes
    assert "localities_persisted=0" in report.summary()
    assert (
        "locality_rows_by_type=(Delivery Area=11, LVR=1, Post Office Boxes=1)" in report.summary()
    )


def test_load_postcodes_persists_and_replaces(
    postcodes_csv: Path, db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    with caplog.at_level(logging.INFO):
        report = load_postcodes(postcodes_csv, repository)
    assert report.postcodes_persisted == 8
    assert report.localities_persisted == 10
    assert report.locality_names_persisted == 10
    assert "postcode load: input_rows=13" in caplog.text
    assert "localities_persisted=10" in caplog.text
    found = repository.coordinates_for(["4101", "4870", "4001", "9999"])
    assert sorted(found) == ["4101", "4870"]
    assert found["4101"].latitude == pytest.approx(-27.484254)
    assert repository.coordinates_for([]) == {}
    # A second load replaces, never appends.
    smaller = write_postcodes(postcodes_csv, POSTCODE_ROWS[:2])
    load_postcodes(smaller, repository)
    assert sorted(repository.coordinates_for(["4000", "4002", "4101", "4870"])) == ["4000"]


def test_load_postcodes_fills_the_locality_gazetteer(localities_csv: Path, db_path: Path) -> None:
    """The loaded gazetteer answers the resolver through the application port, and the
    PO-box and LVR rows with the same locality names are not in it."""
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    load_postcodes(localities_csv, repository)
    resolver = LocalityResolver(repository)
    resolved = resolver("South Bank", state="QLD")
    assert resolved.outcome.value == "resolved"
    assert resolved.locality is not None
    assert (resolved.locality.state, resolved.locality.postcode) == ("QLD", "4101")
    # Spacing is normalised away: "BRISBANE   city" is BRISBANE CITY, QLD 4000.
    brisbane_city = resolver("BRISBANE   city")
    assert brisbane_city.outcome.value == "resolved"
    assert brisbane_city.locality is not None and brisbane_city.locality.postcode == "4000"
    # The gazetteer never carries the PO-box (4001) or LVR (9000) spellings: "brisbane"
    # has exactly one Delivery Area row, so it resolves instead of seeing the boxes.
    brisbane = repository.localities_for("brisbane")
    assert [(item.state, item.postcode) for item in brisbane] == [("QLD", "4000")]


def test_load_postcodes_refuses_low_parse_ratio(tmp_path: Path, db_path: Path) -> None:
    mostly_bad = write_postcodes(
        tmp_path / "bad.csv",
        [POSTCODE_ROWS[0], *["9,4002,BOXES,QLD,0,0" + "," * 44] * 3],
    )
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    with pytest.raises(PostcodesFormatError, match="25.0%"):
        load_postcodes(mostly_bad, repository)
    assert repository.coordinates_for(["4000"]) == {}
    assert repository.localities_for("boxes") == ()


def test_load_script_loads_and_reports_missing_file(
    postcodes_csv: Path, db_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        assert load_postcodes_main(["--csv", str(postcodes_csv), "--db", str(db_path)]) == 0
    assert "postcodes_persisted=8" in caplog.text
    assert "localities_persisted=10" in caplog.text
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    assert sorted(repository.coordinates_for(["4000", "4350"])) == ["4000", "4350"]
    assert load_postcodes_main(["--csv", str(tmp_path / "nope.csv"), "--db", str(db_path)]) == 1
    assert "not found" in caplog.text
    junk = tmp_path / "junk.csv"
    junk.write_text("a,b\n1,2\n", encoding="utf-8")
    assert load_postcodes_main(["--csv", str(junk), "--db", str(db_path)]) == 1
    assert "postcode load failed" in caplog.text
