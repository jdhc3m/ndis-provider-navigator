"""``PageFetcher`` over httpx with robots.txt, per-host pacing and one retry, and the
``TextExtractor`` over trafilatura.

Politeness rules, all enforced here for every request including ``robots.txt`` itself:

- ``robots.txt`` is fetched once per scheme+host and cached for the life of the fetcher. A
  2xx answer is parsed and obeyed (including ``Crawl-delay`` when it is longer than our own
  interval; a delay over ``MAX_CRAWL_DELAY_SECONDS`` is read as "do not crawl" and recorded
  ``crawl_delay_too_long`` without sleeping); a 4xx answer means no rules, so the site is
  fetched (RFC 9309 §2.3.1.3); a 5xx answer, a timeout or a failed connection means the
  rules are unknown, so the site is *not* fetched and the page is recorded
  ``robots_unavailable`` (RFC 9309 §2.3.1.4).
- Requests to one host are at least ``interval`` seconds apart (default 1 s), measured on a
  monotonic clock from the start of the previous request, whatever URL or provider asked.
- Each request has a connect/read timeout (default 10 s); a body over ``max_bytes`` (default
  2 MB) is abandoned as ``too_large``; a body that is not HTML or XHTML is not read at all and
  recorded ``empty``.
- A transient failure (timeout, failed or dropped connection, 5xx) is retried exactly once
  after the usual pacing wait; robots decisions, 4xx answers and oversize pages are not.
  Name resolution failures are not told apart from other connection failures: on a
  proxy-only network the proxy resolves names, so a local lookup would disagree with it.
- Redirects are followed by hand, up to ``MAX_REDIRECTS`` hops, and every hop goes through
  the same robots.txt check and pacing for *its* host, so a redirect cannot lead to a host
  that disallows us or to a burst of requests. ``robots.txt`` itself follows the same number
  of hops (RFC 9309 §2.3.1.2), minus the robots check. More hops end as ``http_error``.
- Every request carries an identifying ``User-Agent`` with a contact URL.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from trafilatura.settings import Document

from ndis.domain.pages import (
    REQUEST_INTERVAL_SECONDS,
    ExtractedText,
    FetchResult,
    FetchStatus,
    is_html_content_type,
    is_transient,
    pacing_delay,
    request_interval,
)

USER_AGENT = "ndis-provider-navigator/0.1 (+https://github.com/jdhc3m/ndis-provider-navigator)"
ROBOTS_TOKEN = "ndis-provider-navigator"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
ROBOTS_PATH = "/robots.txt"


@dataclass(frozen=True)
class _Redirect:
    """A 3xx answer with a ``Location``, already made absolute."""

    location: str
    http_status: int


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
            follow_redirects=False,
            headers={"User-Agent": user_agent},
        )
        self._robots: dict[str, RobotFileParser | FetchStatus] = {}
        self._last_request_at: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def fetch(self, url: str) -> FetchResult:
        current = url
        last_status: int | None = None
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            host = parts.hostname or ""
            if not host:
                return FetchResult(current, FetchStatus.HTTP_ERROR, last_status)
            rules = self._rules_for(parts.scheme, parts.netloc)
            if isinstance(rules, FetchStatus):
                return FetchResult(current, rules)
            if not rules.can_fetch(ROBOTS_TOKEN, current):
                return FetchResult(current, FetchStatus.ROBOTS_DISALLOWED)
            interval = request_interval(self._interval, _crawl_delay(rules))
            if interval is None:
                return FetchResult(current, FetchStatus.CRAWL_DELAY_TOO_LONG)
            outcome = self._request_with_retry(current, host, interval, html_only=True)
            if isinstance(outcome, FetchResult):
                return outcome
            current, last_status = outcome.location, outcome.http_status
        return FetchResult(current, FetchStatus.HTTP_ERROR, last_status)

    def _rules_for(self, scheme: str, netloc: str) -> RobotFileParser | FetchStatus:
        origin = f"{scheme}://{netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_rules(
                urlunsplit((scheme, netloc, ROBOTS_PATH, "", ""))
            )
        return self._robots[origin]

    def _load_rules(self, robots_url: str) -> RobotFileParser | FetchStatus:
        current = robots_url
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname or ""
            if not host:
                return FetchStatus.ROBOTS_UNAVAILABLE
            outcome = self._request_with_retry(current, host, self._interval, html_only=False)
            if isinstance(outcome, FetchResult):
                return _parse_rules(outcome)
            current = outcome.location
        return FetchStatus.ROBOTS_UNAVAILABLE

    def _request_with_retry(
        self, url: str, host: str, interval: float, *, html_only: bool
    ) -> FetchResult | _Redirect:
        outcome = self._request(url, host, interval, html_only=html_only)
        if isinstance(outcome, FetchResult) and is_transient(outcome.status, outcome.http_status):
            outcome = self._request(url, host, interval, html_only=html_only)
        return outcome

    def _pace(self, host: str, interval: float) -> None:
        delay = pacing_delay(self._last_request_at.get(host), self._clock(), interval)
        if delay > 0:
            self._sleep(delay)
        self._last_request_at[host] = self._clock()

    def _request(
        self, url: str, host: str, interval: float, *, html_only: bool
    ) -> FetchResult | _Redirect:
        self._pace(host, interval)
        try:
            with self._client.stream("GET", url) as response:
                code = response.status_code
                if code >= 400:
                    return FetchResult(url, FetchStatus.HTTP_ERROR, code)
                if code >= 300:
                    location = response.headers.get("location")
                    if location is None:
                        return FetchResult(url, FetchStatus.HTTP_ERROR, code)
                    return _Redirect(urljoin(url, location), code)
                if html_only and not is_html_content_type(response.headers.get("content-type")):
                    return FetchResult(url, FetchStatus.EMPTY, code)
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
                    return FetchResult(url, FetchStatus.TOO_LARGE, code)
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        return FetchResult(url, FetchStatus.TOO_LARGE, code)
                    chunks.append(chunk)
                return FetchResult(url, FetchStatus.OK, code, b"".join(chunks))
        except httpx.TimeoutException:
            return FetchResult(url, FetchStatus.TIMEOUT)
        except httpx.TransportError:
            # Includes ``ConnectError``: refused, unreachable or unresolvable hosts and TLS
            # failures all look the same from behind a proxy, so all get the one retry.
            return FetchResult(url, FetchStatus.CONNECTION_ERROR)
        except (httpx.HTTPError, httpx.InvalidURL):
            # A URL httpx cannot send: the server will never see it, so no retry.
            return FetchResult(url, FetchStatus.HTTP_ERROR)


def _parse_rules(result: FetchResult) -> RobotFileParser | FetchStatus:
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


def _crawl_delay(rules: RobotFileParser) -> float | None:
    delay = rules.crawl_delay(ROBOTS_TOKEN)
    return None if delay is None else float(delay)


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
