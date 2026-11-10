"""Pure geography: haversine against independently derived distances, centroid averaging,
the exact-beats-centroid rule, and distance ranking with the inclusive ``max_km`` edge."""

import math

import pytest

from ndis.domain.geo import (
    EARTH_RADIUS_KM,
    Coordinates,
    Distance,
    DistanceSource,
    PostcodeRowError,
    distance_from,
    haversine_km,
    nearest_to,
    parse_postcode_row,
    postcode_centroid,
    rank_by_distance,
)

# Rows of the Australian postcodes CSV (``lat``/``long`` columns) for three postcodes.
BRISBANE_4000 = Coordinates(-27.468391, 153.022126)
WEST_END_4101 = Coordinates(-27.484254, 153.009661)
TOOWOOMBA_4350 = Coordinates(-27.576308, 151.852689)
CAIRNS_4870 = Coordinates(-16.923761, 145.742303)

# Expected distances derived without the haversine formula: the spherical law of cosines
# R·acos(sin φ1 sin φ2 + cos φ1 cos φ2 cos Δλ) on the same R = 6371.0088 km, worked in a
# scratch script. The short hop was also checked by hand on a flat approximation:
# Δφ = 0.015863° × 111.195 km/° = 1.764 km north–south, Δλ = 0.012465° × 111.195 ×
# cos 27.476° = 1.230 km east–west, hypotenuse 2.150 km.
EXPECTED_KM = {
    (BRISBANE_4000, TOOWOOMBA_4350): 115.942,
    (BRISBANE_4000, CAIRNS_4870): 1390.696,
    (BRISBANE_4000, WEST_END_4101): 2.150,
}


def test_earth_radius_is_the_iugg_mean_radius() -> None:
    assert EARTH_RADIUS_KM == 6371.0088


@pytest.mark.parametrize(("a", "b"), list(EXPECTED_KM))
def test_haversine_matches_independently_derived_distances(a: Coordinates, b: Coordinates) -> None:
    expected = EXPECTED_KM[(a, b)]
    assert haversine_km(a, b) == pytest.approx(expected, rel=0.005)
    # Tighter than the acceptance tolerance: the two spherical formulas agree to a metre.
    assert haversine_km(a, b) == pytest.approx(expected, abs=0.001)
    assert haversine_km(b, a) == pytest.approx(haversine_km(a, b))


def test_haversine_of_a_point_with_itself_is_zero_and_antipodes_are_half_a_circumference() -> None:
    assert haversine_km(BRISBANE_4000, BRISBANE_4000) == 0.0
    north, south = Coordinates(90, 0), Coordinates(-90, 0)
    assert haversine_km(north, south) == pytest.approx(math.pi * EARTH_RADIUS_KM)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(90.1, 0), (-90.5, 0), (0, 180.1), (0, -181), (math.nan, 0), (0, math.inf)],
)
def test_coordinates_reject_out_of_range_values(latitude: float, longitude: float) -> None:
    with pytest.raises(ValueError, match="outside"):
        Coordinates(latitude, longitude)


def test_coordinates_accept_the_range_limits() -> None:
    assert Coordinates(90, 180).latitude == 90
    assert Coordinates(-90, -180).longitude == -180


class TestDistanceFrom:
    def test_exact_coordinates_win_over_the_centroid(self) -> None:
        distance = distance_from(BRISBANE_4000, exact=WEST_END_4101, centroid=TOOWOOMBA_4350)
        expected_km = haversine_km(BRISBANE_4000, WEST_END_4101)
        assert distance == Distance(expected_km, DistanceSource.EXACT)

    def test_the_centroid_is_used_when_there_are_no_exact_coordinates(self) -> None:
        distance = distance_from(BRISBANE_4000, exact=None, centroid=TOOWOOMBA_4350)
        assert distance is not None
        assert distance.source is DistanceSource.POSTCODE_CENTROID
        assert distance.km == pytest.approx(115.942, rel=0.005)

    def test_nothing_known_gives_no_distance(self) -> None:
        assert distance_from(BRISBANE_4000, exact=None, centroid=None) is None

    def test_sources_have_exactly_the_two_documented_values(self) -> None:
        assert {source.value for source in DistanceSource} == {"exact", "postcode_centroid"}


def test_nearest_to_picks_the_closest_candidate_and_the_first_of_ties() -> None:
    assert nearest_to(BRISBANE_4000, [TOOWOOMBA_4350, WEST_END_4101, CAIRNS_4870]) == WEST_END_4101
    assert nearest_to(BRISBANE_4000, []) is None
    twin = Coordinates(WEST_END_4101.latitude, WEST_END_4101.longitude)
    first = Coordinates(-27.484254, 153.009661)
    assert nearest_to(BRISBANE_4000, [first, twin]) is first


class TestRanking:
    ITEMS: tuple[tuple[str, Distance | None], ...] = (
        ("far", Distance(12.0, DistanceSource.POSTCODE_CENTROID)),
        ("unknown-a", None),
        ("edge", Distance(5.0, DistanceSource.EXACT)),
        ("near", Distance(1.5, DistanceSource.EXACT)),
        ("unknown-b", None),
        ("edge-twin", Distance(5.0, DistanceSource.POSTCODE_CENTROID)),
    )

    def test_sorts_ascending_with_unmeasured_last_and_ties_in_arrival_order(self) -> None:
        ranked = [name for name, _ in rank_by_distance(self.ITEMS)]
        assert ranked == ["near", "edge", "edge-twin", "far", "unknown-a", "unknown-b"]

    def test_max_km_is_inclusive_at_the_boundary(self) -> None:
        kept = [name for name, _ in rank_by_distance(self.ITEMS, max_km=5.0)]
        assert kept == ["near", "edge", "edge-twin"]

    def test_a_hair_under_the_boundary_drops_the_boundary_items(self) -> None:
        kept = [name for name, _ in rank_by_distance(self.ITEMS, max_km=4.999)]
        assert kept == ["near"]

    def test_max_km_drops_unmeasured_items(self) -> None:
        assert all(distance is not None for _, distance in rank_by_distance(self.ITEMS, 100.0))
        assert rank_by_distance((), max_km=1.0) == ()


class TestPostcodeCentroid:
    def test_the_centroid_is_the_mean_of_every_located_row(self) -> None:
        centroid = postcode_centroid(
            "4000",
            [Coordinates(-27.0, 153.0), Coordinates(-28.0, 153.5), Coordinates(-27.5, 154.0)],
        )
        assert centroid.postcode == "4000"
        assert centroid.locality_count == 3
        assert centroid.coordinates.latitude == pytest.approx(-27.5)
        assert centroid.coordinates.longitude == pytest.approx(153.5)

    def test_a_single_row_is_its_own_centroid(self) -> None:
        assert postcode_centroid("4101", [WEST_END_4101]).coordinates == WEST_END_4101

    def test_no_rows_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="no located rows"):
            postcode_centroid("4101", [])


class TestParsePostcodeRow:
    def test_a_located_row_parses(self) -> None:
        row = {
            "postcode": "4101",
            "locality": "WEST END",
            "lat": "-27.484254",
            "long": "153.009661",
        }
        assert parse_postcode_row(row) == ("4101", WEST_END_4101)

    @pytest.mark.parametrize(
        ("row", "reason"),
        [
            ({"postcode": "4101", "lat": "0", "long": "0"}, "missing_coordinates"),
            ({"postcode": "4101", "lat": "", "long": ""}, "invalid_coordinates"),
            ({"postcode": "4101", "lat": "south", "long": "153"}, "invalid_coordinates"),
            ({"postcode": "4101", "lat": "-95", "long": "153"}, "invalid_coordinates"),
            ({"postcode": "410", "lat": "-27", "long": "153"}, "invalid_postcode"),
            ({"postcode": None, "lat": "-27", "long": "153"}, "invalid_postcode"),
        ],
    )
    def test_unusable_rows_are_rejected_with_a_reason(
        self, row: dict[str, str | None], reason: str
    ) -> None:
        with pytest.raises(PostcodeRowError) as info:
            parse_postcode_row(row)
        assert info.value.reason == reason
