"""Pure rules for collecting provider website evidence: which URL to fetch, whether to fetch
it at all, how to pace and retry, and how page text is hashed and filed.

Nothing here does I/O. The HTTP client, robots.txt parser, clock and file system live in the
adapters; this module decides and the adapters obey.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ndis.domain.entity_resolution import MatchStatus
from ndis.domain.register import website_domain


class FetchStatus(StrEnum):
    """Outcome of one attempt to collect a page; ``OK`` is the only one that yields text."""

    OK = "ok"
    EMPTY = "empty"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    CRAWL_DELAY_TOO_LONG = "crawl_delay_too_long"
    TIMEOUT = "timeout"
    HTTP_ERROR = "http_error"
    CONNECTION_ERROR = "connection_error"
    TOO_LARGE = "too_large"


class PageSource(StrEnum):
    """Where a stored page came from: the earlier Maps crawl or this project's own fetcher."""

    MAPS_CRAWL = "maps_crawl"
    LIVE = "live"


@dataclass(frozen=True)
class ProviderSite:
    """One ``provider_links`` row with both website candidates; the register one is trusted
    first because it is what the provider told the Commission."""

    place_id: str
    abn: str | None
    match_status: MatchStatus
    register_website: str | None
    maps_website: str | None

    @property
    def provider_key(self) -> str:
        """The ABN when the place is linked, otherwise the place id, so two Maps listings of
        one registered provider share one cache."""
        return self.abn or self.place_id


@dataclass(frozen=True)
class CachedPage:
    """An ``ok`` row of the Maps crawl's ``pages`` table; its text is already cleaned."""

    place_id: str
    url: str
    title: str | None
    text: str
    fetched_at: datetime


@dataclass(frozen=True)
class FetchResult:
    """What the fetcher got for one URL: a body only when ``status`` is ``OK``."""

    url: str
    status: FetchStatus
    http_status: int | None = None
    body: bytes | None = None


@dataclass(frozen=True)
class ExtractedText:
    """Boilerplate-free text of a page and its title when the extractor found one."""

    title: str | None
    text: str


@dataclass(frozen=True)
class PageMeta:
    """The public-safe record of one page: everything but the text itself."""

    url: str
    provider_key: str
    fetched_at: datetime
    status: FetchStatus
    source: PageSource
    text_hash: str | None = None
    text_path: str | None = None
    title: str | None = None


REQUEST_INTERVAL_SECONDS = 1.0
MAX_CRAWL_DELAY_SECONDS = 30.0
HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
TRACKING_PARAMETERS = frozenset({"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"})
TRACKING_PREFIX = "utm_"
TEXT_PATH_HASH_LENGTH = 16
TEXT_FILE_SUFFIX = ".txt"


def normalise_url(url: str | None) -> str | None:
    """Canonical http(s) URL or ``None`` when ``url`` is not a usable web address.

    Adds ``https://`` when the scheme is missing, lower-cases scheme and host, drops the
    fragment and tracking parameters (``utm_*``, ``gclid``, …), and gives a bare host the
    path ``/``. Anything without a dotted host, or with a non-http scheme, is rejected.
    """
    if url is None or not url.strip():
        return None
    candidate = url.strip()
    scheme = urlsplit(candidate).scheme.lower()
    if not scheme:
        candidate = "https://" + candidate
    elif scheme not in {"http", "https"}:
        return None
    parts = urlsplit(candidate)
    if website_domain(candidate) is None:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host if port is None else f"{host}:{port}"
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_parameter(key)
    ]
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", urlencode(kept), ""))


def _is_tracking_parameter(key: str) -> bool:
    return key.lower().startswith(TRACKING_PREFIX) or key.lower() in TRACKING_PARAMETERS


def choose_website(register_website: str | None, maps_website: str | None) -> str | None:
    """The URL to fetch for a provider: the register's website when usable, else Maps'."""
    return normalise_url(register_website) or normalise_url(maps_website)


def wants_evidence(status: MatchStatus) -> bool:
    """False for places the matcher decided are not providers; their sites hold no evidence."""
    return status is not MatchStatus.NOT_A_PROVIDER


def should_fetch(has_cached_ok: bool, refresh: bool) -> bool:
    """Fetch live only when nothing usable is cached, or when a refresh was asked for."""
    return refresh or not has_cached_ok


def is_transient(status: FetchStatus, http_status: int | None) -> bool:
    """True for failures worth one retry: a timeout, a failed or dropped connection or a 5xx
    answer. Robots decisions, 4xx answers and oversize pages will not change."""
    if status in {FetchStatus.TIMEOUT, FetchStatus.CONNECTION_ERROR}:
        return True
    return status is FetchStatus.HTTP_ERROR and http_status is not None and http_status >= 500


def request_interval(interval: float, crawl_delay: float | None) -> float | None:
    """The gap to keep between requests to a host: our own ``interval`` or the site's
    ``Crawl-delay`` when longer; ``None`` when the site asks for more than
    ``MAX_CRAWL_DELAY_SECONDS``, which means it does not want to be crawled by us at all."""
    if crawl_delay is None:
        return interval
    if crawl_delay > MAX_CRAWL_DELAY_SECONDS:
        return None
    return max(interval, crawl_delay)


def is_html_content_type(content_type: str | None) -> bool:
    """True for HTML and XHTML media types; a missing header is taken as HTML, anything
    else (PDF, images, JSON, plain text) holds nothing the extractor should see."""
    if content_type is None or not content_type.strip():
        return True
    return content_type.split(";", 1)[0].strip().lower() in HTML_CONTENT_TYPES


def pacing_delay(last_request_at: float | None, now: float, interval: float) -> float:
    """Seconds to wait so that consecutive requests to one host are ``interval`` apart."""
    if last_request_at is None:
        return 0.0
    return max(0.0, last_request_at + interval - now)


def normalise_text(text: str) -> str:
    """Every line stripped of surrounding whitespace, blank runs collapsed, no outer blanks."""
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    while collapsed and not collapsed[-1]:
        collapsed.pop()
    return "\n".join(collapsed)


def text_hash(text: str) -> str:
    """SHA-256 hex digest of the normalised text, so the same page content hashes equal."""
    return hashlib.sha256(normalise_text(text).encode("utf-8")).hexdigest()


def text_path(url: str) -> str:
    """File name (relative to the private pages directory) for a URL's text."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return digest[:TEXT_PATH_HASH_LENGTH] + TEXT_FILE_SUFFIX
