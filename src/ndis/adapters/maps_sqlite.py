"""Read the private Google Maps export (``data/private/maps_brisbane.db``) as MapsPlace and
CachedPage values.

Only the ``providers`` table (the columns the matcher needs) and the ``ok`` rows of the
``pages`` table (the earlier crawl's cleaned text) are read; ``reviews`` is never touched. The
file itself must stay under ``data/private/``.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ndis.domain.entity_resolution import MapsPlace
from ndis.domain.pages import CachedPage
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
PAGE_STATUS_OK = "ok"


class MapsExportError(RuntimeError):
    """The export is missing or does not have the expected ``providers`` table."""


def _blank_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_rows(path: Path, query: str) -> list[tuple[object, ...]]:
    if not path.is_file():
        raise MapsExportError(f"Maps export not found at {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            return connection.execute(query).fetchall()
        except sqlite3.OperationalError as exc:
            raise MapsExportError(f"{path} is not a Maps export: {exc}") from exc
    finally:
        connection.close()


class SqliteMapsPlaceSource:
    """``MapsPlaceSource`` over the scraper's SQLite file, read-only."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def places(self) -> tuple[MapsPlace, ...]:
        query = f"select {', '.join(PLACE_COLUMNS)} from providers order by place_id"
        rows = _read_rows(self._path, query)
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


class SqliteMapsPageSource:
    """``CachedPageSource`` over the crawl's ``pages`` table, read-only; ``ok`` rows only."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def ok_pages(self) -> tuple[CachedPage, ...]:
        query = (
            "select place_id, url, title, text, fetched_at from pages "
            f"where status = '{PAGE_STATUS_OK}' order by place_id, url"
        )
        return tuple(
            CachedPage(
                place_id=str(place_id),
                url=str(url).strip(),
                title=_blank_to_none(title),
                text="" if text is None else str(text),
                fetched_at=_parse_fetched_at(str(fetched_at)),
            )
            for place_id, url, title, text, fetched_at in _read_rows(self._path, query)
        )


class SqliteRegexBaseline:
    """``BaselineSource`` over the crawl's regex hits: every place's hit slugs, read-only."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def baseline(self) -> dict[str, frozenset[str]]:
        rows = _read_rows(
            self._path,
            "select ps.place_id, s.slug from provider_services ps "
            "join services s on s.id = ps.service_id order by ps.place_id, s.slug",
        )
        slugs: dict[str, set[str]] = {}
        for place_id, slug in rows:
            slugs.setdefault(str(place_id), set()).add(str(slug))
        return {place_id: frozenset(hits) for place_id, hits in slugs.items()}


def _parse_fetched_at(value: str) -> datetime:
    """The crawl stamps ISO-8601 with an offset; a stamp without one is taken as UTC."""
    parsed = datetime.fromisoformat(value.strip())
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
