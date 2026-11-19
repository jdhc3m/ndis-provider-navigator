"""The pure locality resolver: normalisation, the three outcomes, and the narrowing by a
stated state or by the Brisbane-area flag.

Every expected value is read from the fixture CSV (the same rows the real
``australian_postcodes.csv`` carries for these names), never hard-coded in the test.
"""

import csv
from pathlib import Path

import pytest

from ndis.adapters.postcodes_csv import load_postcodes
from ndis.adapters.sqlite_repository import SqlAlchemyPostcodeCentroids, create_sqlite_engine
from ndis.application.providers import LocalityResolver
from ndis.domain.geo import COLUMN_POSTCODE, NON_GEOGRAPHIC_POSTCODES
from ndis.domain.locality import (
    Locality,
    LocalityOutcome,
    LocalityResolution,
    normalise_locality,
    parse_locality_row,
    resolve_locality,
)
from tests.conftest import LocalityDirectoryStub


@pytest.fixture
def csv_rows(localities_csv: Path) -> list[dict[str, str]]:
    """The fixture file's rows, read back the way the source carries them."""
    with localities_csv.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def stub(csv_rows: list[dict[str, str]]) -> LocalityDirectoryStub:
    return LocalityDirectoryStub(
        [parse_locality_row(row) for row in csv_rows if row["type"] == "Delivery Area"]
    )


def csv_pairs(csv_rows: list[dict[str, str]], name: str) -> set[tuple[str, str]]:
    """Every (state, postcode) the CSV carries under any spelling of ``name``."""
    key = normalise_locality(name)
    return {
        (row["state"], row[COLUMN_POSTCODE])
        for row in csv_rows
        if row["type"] == "Delivery Area"
        and normalise_locality(row["locality"]) == key
        and row[COLUMN_POSTCODE] not in NON_GEOGRAPHIC_POSTCODES
    }


def exact_pairs(csv_rows: list[dict[str, str]], name: str) -> set[tuple[str, str]]:
    """The (state, postcode) pairs of the rows spelled exactly like ``name`` (up to case)."""
    return {
        (row["state"], row[COLUMN_POSTCODE])
        for row in csv_rows
        if row["type"] == "Delivery Area" and row["locality"].casefold() == name.casefold()
    }


def csv_names(csv_rows: list[dict[str, str]], name: str) -> set[str]:
    """Every spelling the CSV carries for ``name``'s key."""
    key = normalise_locality(name)
    return {
        row["locality"]
        for row in csv_rows
        if row["type"] == "Delivery Area" and normalise_locality(row["locality"]) == key
    }


def pair_of(resolution: LocalityResolution) -> tuple[str, str] | None:
    return (
        (resolution.locality.state, resolution.locality.postcode) if resolution.locality else None
    )


def candidate_pairs(resolution: LocalityResolution) -> set[tuple[str, str]]:
    return {(item.state, item.postcode) for item in resolution.candidates}


def test_normalisation_collapses_case_spacing_and_punctuation() -> None:
    assert normalise_locality("South Bank") == normalise_locality("SOUTHBANK")
    assert normalise_locality("Southbank") == normalise_locality("south bank")
    assert normalise_locality("O'Connor") == normalise_locality("oconnor")
    assert normalise_locality("St-Lucia") == normalise_locality("st lucia")
    assert normalise_locality("  WEST   END  ") == "westend"
    assert normalise_locality("...") == ""


class TestResolveFromTheCSV:
    def test_south_bank_with_a_state_resolves(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("South Bank", stub.localities_for("southbank"), state="QLD")
        assert resolution.outcome is LocalityOutcome.RESOLVED
        # The QLD spelling the CSV carries for this place is SOUTH BANK itself.
        assert exact_pairs(csv_rows, "South Bank") == {("QLD", "4101")}
        assert pair_of(resolution) == ("QLD", "4101")

    def test_southbank_and_south_bank_are_ambiguous_without_a_state(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        for name in ("Southbank", "South Bank"):
            resolution = resolve_locality(name, stub.localities_for(normalise_locality(name)))
            assert resolution.outcome is LocalityOutcome.AMBIGUOUS
            assert candidate_pairs(resolution) == csv_pairs(csv_rows, name)
            assert csv_pairs(csv_rows, name) == {("QLD", "4101"), ("VIC", "3006")}
            # Each candidate keeps the CSV spelling of its own place.
            assert {item.name for item in resolution.candidates} <= csv_names(csv_rows, name)

    def test_southbank_with_vic_resolves_to_its_own_row(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("Southbank", stub.localities_for("southbank"), state="VIC")
        assert resolution.outcome is LocalityOutcome.RESOLVED
        assert exact_pairs(csv_rows, "Southbank") == {("VIC", "3006")}
        assert pair_of(resolution) == ("VIC", "3006")

    def test_west_end_has_three_candidates(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("West End", stub.localities_for("westend"))
        assert resolution.outcome is LocalityOutcome.AMBIGUOUS
        assert len(resolution.candidates) == 3
        assert candidate_pairs(resolution) == csv_pairs(csv_rows, "West End")
        assert candidate_pairs(resolution) == {("QLD", "4101"), ("QLD", "4810"), ("WA", "6530")}
        # Deterministic order: sorted by state then postcode.
        assert [(item.state, item.postcode) for item in resolution.candidates] == [
            ("QLD", "4101"),
            ("QLD", "4810"),
            ("WA", "6530"),
        ]

    def test_west_end_with_the_brisbane_area_resolves(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality(
            "West End", stub.localities_for("westend"), brisbane_area=True
        )
        assert resolution.outcome is LocalityOutcome.RESOLVED
        assert pair_of(resolution) == ("QLD", "4101")
        assert csv_pairs(csv_rows, "West End") == {("QLD", "4101"), ("QLD", "4810"), ("WA", "6530")}
        assert pair_of(resolution) in csv_pairs(csv_rows, "West End")

    def test_west_end_with_qld_stays_ambiguous(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("West End", stub.localities_for("westend"), state="QLD")
        assert resolution.outcome is LocalityOutcome.AMBIGUOUS
        assert candidate_pairs(resolution) == {("QLD", "4101"), ("QLD", "4810")}
        assert candidate_pairs(resolution) == csv_pairs(csv_rows, "West End") & {
            ("QLD", postcode) for _, postcode in csv_pairs(csv_rows, "West End")
        }

    def test_west_end_with_a_state_it_is_not_in_is_unknown(
        self, stub: LocalityDirectoryStub
    ) -> None:
        resolution = resolve_locality("West End", stub.localities_for("westend"), state="TAS")
        assert resolution.outcome is LocalityOutcome.UNKNOWN
        assert resolution.candidates == ()

    def test_brisbane_city_resolves(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("Brisbane City", stub.localities_for("brisbanecity"))
        assert resolution.outcome is LocalityOutcome.RESOLVED
        assert exact_pairs(csv_rows, "Brisbane City") == {("QLD", "4000")}
        assert pair_of(resolution) == ("QLD", "4000")

    def test_brisbane_resolves_to_its_delivery_area_row(
        self, stub: LocalityDirectoryStub, csv_rows: list[dict[str, str]]
    ) -> None:
        resolution = resolve_locality("Brisbane", stub.localities_for("brisbane"))
        assert resolution.outcome is LocalityOutcome.RESOLVED
        assert exact_pairs(csv_rows, "Brisbane") == {("QLD", "4000")}
        assert pair_of(resolution) == ("QLD", "4000")
        # The 4001 PO-box row and the 9000 LVR row spell BRISBANE too, but only the
        # Delivery Area row is a place, so it is the key's only candidate.
        spellings = csv_names(csv_rows, "Brisbane")
        assert "BRISBANE" in spellings

    def test_an_unknown_name_is_unknown(self, stub: LocalityDirectoryStub) -> None:
        resolution = resolve_locality("Gotham City", stub.localities_for("gothamcity"))
        assert resolution.outcome is LocalityOutcome.UNKNOWN
        assert resolution.locality is None
        assert resolution.candidates == ()
        assert resolution.name == "Gotham City"

    def test_no_candidates_at_all_is_unknown(self) -> None:
        resolution = resolve_locality("Whites Hill", ())
        assert resolution.outcome is LocalityOutcome.UNKNOWN


def test_the_directory_key_ignores_the_question_s_spacing() -> None:
    directory = LocalityDirectoryStub([Locality("SOUTH BANK", "QLD", "4101")])
    assert directory.localities_for(normalise_locality("south   bank")) == (
        Locality("SOUTH BANK", "QLD", "4101"),
    )
    assert directory.localities_for("southbank") == (Locality("SOUTH BANK", "QLD", "4101"),)


def test_a_user_spelling_that_matches_a_candidate_is_its_representative() -> None:
    candidates = [
        Locality("SOUTH BANK", "QLD", "4101"),
        Locality("SOUTHBANK", "VIC", "3006"),
    ]
    resolution = resolve_locality("Southbank", candidates, state="VIC")
    assert resolution.locality is not None and resolution.locality.name == "SOUTHBANK"
    other = resolve_locality("South Bank", candidates, state="QLD")
    assert other.locality is not None and other.locality.name == "SOUTH BANK"


def test_parse_locality_row_rejects_non_place_rows() -> None:
    from ndis.domain.locality import LocalityRowError

    delivery = {
        "postcode": "4101",
        "locality": "SOUTH BANK",
        "state": "QLD",
        "type": "Delivery Area",
    }
    assert parse_locality_row(delivery) == Locality("SOUTH BANK", "QLD", "4101")
    for row, reason in (
        (delivery | {"type": "Post Office Boxes"}, "not_delivery_area"),
        (delivery | {"type": "LVR"}, "not_delivery_area"),
        (delivery | {"type": ""}, "not_delivery_area"),
        (delivery | {"locality": " "}, "missing_locality"),
        (delivery | {"state": "ZZ"}, "invalid_state"),
        (delivery | {"postcode": "410"}, "invalid_postcode"),
        (delivery | {"postcode": "9999", "locality": "NORTH POLE"}, "non_geographic"),
    ):
        with pytest.raises(LocalityRowError) as raised:
            parse_locality_row(row)
        assert raised.value.reason == reason


def test_the_loaded_store_resolves_through_the_port(
    localities_csv: Path, db_path: Path, csv_rows: list[dict[str, str]]
) -> None:
    """The full path: fixture CSV -> loader -> SQLite gazetteer -> resolver port."""
    repository = SqlAlchemyPostcodeCentroids(create_sqlite_engine(db_path))
    load_postcodes(localities_csv, repository)
    resolver = LocalityResolver(repository)
    west_end = resolver("West End")
    assert west_end.outcome is LocalityOutcome.AMBIGUOUS
    assert candidate_pairs(west_end) == csv_pairs(csv_rows, "West End")
    narrow = resolver("West End", brisbane_area=True)
    assert narrow.outcome is LocalityOutcome.RESOLVED
    assert pair_of(narrow) == ("QLD", "4101")
    unknown = resolver("Nowhereville")
    assert unknown.outcome is LocalityOutcome.UNKNOWN
