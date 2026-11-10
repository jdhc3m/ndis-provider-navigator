"""Pure geography: coordinates, great-circle distance, postcode centroids and distance
ranking. Nothing here knows where coordinates come from; the application layer decides
which coordinates a provider gets and labels the result with its ``DistanceSource``."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

# Mean Earth radius R1 = (2a + b) / 3 of the WGS 84 ellipsoid (a = 6378.137 km,
# b = 6356.7523 km), as recommended by the IUGG; the usual choice for haversine distances.
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Coordinates:
    """A WGS 84 point in decimal degrees. Latitude is −90..90, longitude −180..180."""

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.latitude) and -90.0 <= self.latitude <= 90.0):
            raise ValueError(f"latitude {self.latitude!r} is outside -90..90")
        if not (math.isfinite(self.longitude) and -180.0 <= self.longitude <= 180.0):
            raise ValueError(f"longitude {self.longitude!r} is outside -180..180")


def haversine_km(a: Coordinates, b: Coordinates) -> float:
    """Great-circle distance between ``a`` and ``b`` in kilometres on a sphere of radius
    ``EARTH_RADIUS_KM`` (the haversine formula). Straight-line, not a driving distance;
    the spherical model differs from the ellipsoid by well under 0.5 % at these scales."""
    lat1, lat2 = math.radians(a.latitude), math.radians(b.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(b.longitude - a.longitude)
    h = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


class DistanceSource(StrEnum):
    """Which coordinates a provider's distance was measured from."""

    EXACT = "exact"
    """The provider's Google Maps listing coordinates (a resolved ``registered`` link)."""
    POSTCODE_CENTROID = "postcode_centroid"
    """The centroid of the provider's head-office postcode: approximate, tens of kilometres
    off in large rural postcodes."""


@dataclass(frozen=True)
class Distance:
    """A measured distance and how it was measured."""

    km: float
    source: DistanceSource


@dataclass(frozen=True)
class PostcodeCentroid:
    """The representative point of one postcode and how many locality rows produced it."""

    postcode: str
    coordinates: Coordinates
    locality_count: int


COLUMN_POSTCODE = "postcode"
COLUMN_LATITUDE = "lat"
COLUMN_LONGITUDE = "long"
POSTCODE_COLUMNS = (COLUMN_POSTCODE, COLUMN_LATITUDE, COLUMN_LONGITUDE)


class PostcodeRowError(ValueError):
    """A postcode CSV row without a usable location; ``reason`` is a short slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_postcode_row(row: Mapping[str, str | None]) -> tuple[str, Coordinates]:
    """One ``(postcode, point)`` from a row of the Australian postcodes CSV.

    The source writes ``0`` for ``lat``/``long`` when a locality has no location, so a zero
    pair is ``missing_coordinates``, not a point in the Gulf of Guinea. A postcode that is
    not four digits is ``invalid_postcode``; text that is not a number, or a number outside
    the WGS 84 ranges, is ``invalid_coordinates``.
    """
    postcode = (row.get(COLUMN_POSTCODE) or "").strip()
    if not (postcode.isdigit() and len(postcode) == 4):
        raise PostcodeRowError("invalid_postcode")
    try:
        latitude = float((row.get(COLUMN_LATITUDE) or "").strip())
        longitude = float((row.get(COLUMN_LONGITUDE) or "").strip())
    except ValueError as exc:
        raise PostcodeRowError("invalid_coordinates") from exc
    if latitude == 0.0 and longitude == 0.0:
        raise PostcodeRowError("missing_coordinates")
    try:
        return postcode, Coordinates(latitude, longitude)
    except ValueError as exc:
        raise PostcodeRowError("invalid_coordinates") from exc


def postcode_centroid(postcode: str, points: Sequence[Coordinates]) -> PostcodeCentroid:
    """The arithmetic mean of a postcode's locality points.

    A postcode CSV lists one row per locality (suburb, PO box, large-volume receiver) and
    the row order carries no meaning, so the first row would be an arbitrary pick; the mean
    of every located row is a stable approximation of the postcode's centre. Every
    Australian postcode is far from the antimeridian, so a plain mean of longitudes is safe.
    """
    if not points:
        raise ValueError(f"postcode {postcode!r} has no located rows")
    return PostcodeCentroid(
        postcode=postcode,
        coordinates=Coordinates(
            latitude=sum(point.latitude for point in points) / len(points),
            longitude=sum(point.longitude for point in points) / len(points),
        ),
        locality_count=len(points),
    )


def nearest_to(origin: Coordinates, candidates: Iterable[Coordinates]) -> Coordinates | None:
    """The candidate closest to ``origin`` (the first of equally near ones), or ``None``."""
    nearest: Coordinates | None = None
    nearest_km = math.inf
    for candidate in candidates:
        km = haversine_km(origin, candidate)
        if km < nearest_km:
            nearest, nearest_km = candidate, km
    return nearest


def distance_from(
    origin: Coordinates, exact: Coordinates | None, centroid: Coordinates | None
) -> Distance | None:
    """A provider's distance from ``origin``: measured from its exact coordinates when it
    has any, from its head-office postcode centroid otherwise, ``None`` when it has neither.
    The result says which was used; a centroid never masquerades as exact."""
    if exact is not None:
        return Distance(haversine_km(origin, exact), DistanceSource.EXACT)
    if centroid is not None:
        return Distance(haversine_km(origin, centroid), DistanceSource.POSTCODE_CENTROID)
    return None


def rank_by_distance[T](
    items: Sequence[tuple[T, Distance | None]], max_km: float | None = None
) -> tuple[tuple[T, Distance | None], ...]:
    """Sort ``items`` by distance ascending with unmeasured items last.

    The sort is stable: items at the same distance, and all unmeasured items, keep the
    order they arrived in (the repository's business-name-then-ABN order). ``max_km`` is
    inclusive — an item at exactly ``max_km`` is kept, one a hair further is dropped — and
    it drops unmeasured items too, because nothing says they are within range.
    """
    kept = items
    if max_km is not None:
        kept = [
            (item, distance)
            for item, distance in items
            if distance is not None and distance.km <= max_km
        ]
    return tuple(sorted(kept, key=lambda pair: (pair[1] is None, pair[1].km if pair[1] else 0.0)))
