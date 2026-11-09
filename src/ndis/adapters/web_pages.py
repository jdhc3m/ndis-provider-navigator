"""``PageFetcher`` over httpx with robots.txt, per-host pacing and one retry, and the
``TextExtractor`` over trafilatura.

Politeness rules, all enforced here for every request including ``robots.txt`` itself:

- ``robots.txt`` is fetched once per scheme+host and cached for the life of the fetcher. A
  2xx answer is parsed and obeyed (including ``Crawl-delay`` when it is longer than our own
  interval); a 4xx answer means no rules, so the site is fetched (RFC 9309 §2.3.1.3); a 5xx
  answer, a timeout or a dropped connection means the rules are unknown, so the site is
  *not* fetched and the page is recorded ``robots_unavailable`` (RFC 9309 §2.3.1.4).
- Requests to one host are at least ``interval`` seconds apart (default 1 s), measured on a
  monotonic clock from the start of the previous request, whatever URL or provider asked.
- Each request has a connect/read timeout (default 10 s); a body over ``max_bytes`` (default
  2 MB) is abandoned as ``too_large``.
- A transient failure (timeout, dropped connection, 5xx) is retried exactly once after the
  usual pacing wait; robots decisions, DNS failures, 4xx answers and oversize pages are not.
- Every request carries an identifying ``User-Agent`` with a contact URL.

Redirects are followed by httpx; robots.txt and pacing are applied to the host of the URL
asked for, not to a host a redirect may lead to.
"""

import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from trafilatura.settings import Document

from ndis.domain.pages import (
    REQUEST_INTERVAL_SECONDS,
    ExtractedText,
    FetchResult,
    FetchStatus,
    is_transient,
    pacing_delay,
)

USER_AGENT = "ndis-provider-navigator/0.1 (+https://github.com/jdhc3m/ndis-provider-navigator)"
ROBOTS_TOKEN = "ndis-provider-navigator"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_PAGE_BYTES = 2 * 1024 * 1024
ROBOTS_PATH = "/robots.txt"


class HttpxPageFetcher:
    """Polite ``PageFetcher``; one instance shares its robots cache and pacing across a run.

    ``clock`` and ``sleep`` are injectable for tests; production uses ``time.monotonic`` and
    ``time.sleep``.
    """

    def __init__(
        self,
        *,
        interval: float = REQUEST_INTERVAL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_PAGE_BYTES,
        user_agent: str = USER_AGENT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = interval
        self._max_bytes = max_bytes
        self._clock = clock
        self._sleep = sleep
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        self._robots: dict[str, RobotFileParser | FetchStatus] = {}
        self._last_request_at: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def fetch(self, url: str) -> FetchResult:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if not _resolves(host):
            return FetchResult(url, FetchStatus.DNS_ERROR)
        rules = self._rules_for(parts.scheme, parts.netloc, host)
        if isinstance(rules, FetchStatus):
            return FetchResult(url, rules)
        if not rules.can_fetch(ROBOTS_TOKEN, url):
            return FetchResult(url, FetchStatus.ROBOTS_DISALLOWED)
        interval = max(self._interval, rules.crawl_delay(ROBOTS_TOKEN) or 0.0)
        result = self._request(url, host, interval)
        if is_transient(result.status, result.http_status):
            result = self._request(url, host, interval)
        return result

    def _rules_for(self, scheme: str, netloc: str, host: str) -> RobotFileParser | FetchStatus:
        origin = f"{scheme}://{netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_rules(
                urlunsplit((scheme, netloc, ROBOTS_PATH, "", "")), host
            )
        return self._robots[origin]

    def _load_rules(self, robots_url: str, host: str) -> RobotFileParser | FetchStatus:
        result = self._request(robots_url, host, self._interval)
        if is_transient(result.status, result.http_status):
            result = self._request(robots_url, host, self._interval)
        parser = RobotFileParser()
        if result.status is FetchStatus.OK and result.body is not None:
            parser.parse(result.body.decode("utf-8", errors="replace").splitlines())
            return parser
        if result.status is FetchStatus.HTTP_ERROR and result.http_status is not None:
            if result.http_status < 500:
                parser.allow_all = True
                return parser
        if result.status is FetchStatus.TOO_LARGE:
            parser.allow_all = True
            return parser
        return FetchStatus.ROBOTS_UNAVAILABLE

    def _pace(self, host: str, interval: float) -> None:
        delay = pacing_delay(self._last_request_at.get(host), self._clock(), interval)
        if delay > 0:
            self._sleep(delay)
        self._last_request_at[host] = self._clock()

    def _request(self, url: str, host: str, interval: float) -> FetchResult:
        self._pace(host, interval)
        try:
            with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return FetchResult(url, FetchStatus.HTTP_ERROR, response.status_code)
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                    return FetchResult(url, FetchStatus.TOO_LARGE, response.status_code)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        return FetchResult(url, FetchStatus.TOO_LARGE, response.status_code)
                    chunks.append(chunk)
                return FetchResult(url, FetchStatus.OK, response.status_code, b"".join(chunks))
        except httpx.TimeoutException:
            return FetchResult(url, FetchStatus.TIMEOUT)
        except httpx.TransportError:
            return FetchResult(url, FetchStatus.CONNECTION_ERROR)
        except httpx.HTTPError:
            # Too many redirects or a malformed URL: the server will answer the same way again.
            return FetchResult(url, FetchStatus.HTTP_ERROR)


def _resolves(host: str) -> bool:
    if not host:
        return False
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return True


class TrafilaturaExtractor:
    """``TextExtractor`` over ``trafilatura.bare_extraction``: main text without navigation,
    footers or comments, with the page title when trafilatura finds one."""

    def extract(self, body: bytes, url: str) -> ExtractedText:
        document = trafilatura.bare_extraction(
            body, url=url, include_comments=False, with_metadata=True
        )
        if not isinstance(document, Document):
            return ExtractedText(None, "")
        return ExtractedText(document.title or None, document.text or "")
