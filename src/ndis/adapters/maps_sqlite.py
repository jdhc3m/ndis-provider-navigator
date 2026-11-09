"""Read the private Google Maps export (``data/private/maps_brisbane.db``) as MapsPlace values.

Only the ``providers`` table is read and only the columns the matcher needs; ``reviews`` and
the crawled pages are never touched. The file itself must stay under ``data/private/``.
"""

import sqlite3
from pathlib import Path

from ndis.domain.entity_resolution import MapsPlace
from ndis.domain.register import website_domain

PLACE_COLUMNS = (
    "place_id",
    "title",
    "category",
    "website",
    "suburb",
    "postcode",
    "latitude",
    "longitude",
)


class MapsExportError(RuntimeError):
    """The export is missing or does not have the expected ``providers`` table."""


def _blank_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class SqliteMapsPlaceSource:
    """``MapsPlaceSource`` over the scraper's SQLite file, read-only."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def places(self) -> tuple[MapsPlace, ...]:
        if not self._path.is_file():
            raise MapsExportError(f"Maps export not found at {self._path}")
        query = f"select {', '.join(PLACE_COLUMNS)} from providers order by place_id"
        connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        try:
            try:
                rows = connection.execute(query).fetchall()
            except sqlite3.OperationalError as exc:
                raise MapsExportError(f"{self._path} is not a Maps export: {exc}") from exc
        finally:
            connection.close()
        places = []
        for place_id, title, category, website, suburb, postcode, latitude, longitude in rows:
            website = _blank_to_none(website)
            places.append(
                MapsPlace(
                    place_id=str(place_id),
                    title=str(title).strip(),
                    category=_blank_to_none(category),
                    website=website,
                    website_domain=website_domain(website),
                    suburb=_blank_to_none(suburb),
                    postcode=_blank_to_none(postcode),
                    latitude=None if latitude is None else float(latitude),
                    longitude=None if longitude is None else float(longitude),
                )
            )
        return tuple(places)
