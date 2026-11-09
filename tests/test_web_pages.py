"""The httpx fetcher against a real local HTTP server: robots.txt, per-host pacing, timeout
and retry, size cap, DNS failures and the identifying User-Agent; the trafilatura extractor
on HTML with navigation and footer boilerplate."""

from collections.abc import Iterator

import pytest

from ndis.adapters.web_pages import (
    ROBOTS_TOKEN,
    USER_AGENT,
    HttpxPageFetcher,
    TrafilaturaExtractor,
)
from ndis.domain.pages import REQUEST_INTERVAL_SECONDS, FetchStatus
from tests.conftest import PAGE_HTML, LocalSite

FAST = 0.01
SHORT_TIMEOUT = 0.3


@pytest.fixture
def fetcher() -> Iterator[HttpxPageFetcher]:
    fetcher = HttpxPageFetcher(interval=FAST, timeout=SHORT_TIMEOUT)
    yield fetcher
    fetcher.close()


def gaps(site: LocalSite) -> list[float]:
    times = [at for _, at, _ in site.requests]
    return [later - earlier for earlier, later in zip(times, times[1:], strict=False)]


def test_allowed_page_is_fetched_once_with_robots_read_first(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    local_site.robots = "User-agent: *\nDisallow: /private\n"
    result = fetcher.fetch(local_site.url("/page"))
    assert (result.status, result.http_status) == (FetchStatus.OK, 200)
    assert result.body == PAGE_HTML.replace(b"{name}", b"page")
    assert local_site.paths() == ["/robots.txt", "/page"]
    assert {agent for _, _, agent in local_site.requests} == {USER_AGENT}
    assert ROBOTS_TOKEN in USER_AGENT and "+https://" in USER_AGENT


def test_robots_disallow_stops_the_page_request_and_rules_are_cached_per_host(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    local_site.robots = f"User-agent: {ROBOTS_TOKEN}\nDisallow: /private\n"
    result = fetcher.fetch(local_site.url("/private/secret"))
    assert (result.status, result.body) == (FetchStatus.ROBOTS_DISALLOWED, None)
    assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    assert local_site.paths() == ["/robots.txt", "/page"]


def test_missing_robots_means_no_rules(local_site: LocalSite, fetcher: HttpxPageFetcher) -> None:
    local_site.robots = None
    assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    assert local_site.paths() == ["/robots.txt", "/page"]


def test_robots_server_error_is_retried_once_then_blocks_the_host(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    local_site.robots_status = 503
    assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.ROBOTS_UNAVAILABLE
    assert fetcher.fetch(local_site.url("/other")).status is FetchStatus.ROBOTS_UNAVAILABLE
    assert local_site.paths() == ["/robots.txt", "/robots.txt"]


def test_robots_timeout_blocks_the_host(local_site: LocalSite, fetcher: HttpxPageFetcher) -> None:
    local_site.robots_delay = 1.0
    assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.ROBOTS_UNAVAILABLE
    assert local_site.paths() == ["/robots.txt", "/robots.txt"]


def test_requests_to_one_host_are_paced_at_the_default_interval(local_site: LocalSite) -> None:
    fetcher = HttpxPageFetcher(timeout=SHORT_TIMEOUT)
    try:
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
        assert fetcher.fetch(local_site.url("/other")).status is FetchStatus.OK
    finally:
        fetcher.close()
    assert local_site.paths() == ["/robots.txt", "/page", "/other"]
    assert all(gap >= REQUEST_INTERVAL_SECONDS for gap in gaps(local_site))
    assert all(gap < REQUEST_INTERVAL_SECONDS + 0.5 for gap in gaps(local_site))


def test_pacing_is_per_host_and_shared_across_calls(local_site: LocalSite) -> None:
    waits: list[float] = []
    now = [1000.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    fetcher = HttpxPageFetcher(interval=2.5, timeout=SHORT_TIMEOUT, clock=clock, sleep=sleep)
    try:
        fetcher.fetch(local_site.url("/page"))
        fetcher.fetch(local_site.url("/other"))
        fetcher.fetch("http://nonexistent.invalid/page")
    finally:
        fetcher.close()
    # robots.txt, /page and /other share the host: two waits of the full interval; the
    # unresolvable host never reaches the pacing step.
    assert waits == [2.5, 2.5]
    assert local_site.paths() == ["/robots.txt", "/page", "/other"]


def test_crawl_delay_longer_than_our_interval_is_honoured(local_site: LocalSite) -> None:
    waits: list[float] = []
    now = [0.0]

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    local_site.robots = f"User-agent: {ROBOTS_TOKEN}\nCrawl-delay: 4\n"
    fetcher = HttpxPageFetcher(interval=1.0, timeout=SHORT_TIMEOUT, clock=clock, sleep=sleep)
    try:
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    finally:
        fetcher.close()
    assert waits == [4.0]


def test_timeout_is_retried_exactly_once(local_site: LocalSite, fetcher: HttpxPageFetcher) -> None:
    local_site.slow_seconds = 1.0
    result = fetcher.fetch(local_site.url("/slow"))
    assert (result.status, result.http_status, result.body) == (FetchStatus.TIMEOUT, None, None)
    assert local_site.paths() == ["/robots.txt", "/slow", "/slow"]


def test_server_error_is_retried_once_then_reported(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    result = fetcher.fetch(local_site.url("/500"))
    assert (result.status, result.http_status, result.body) == (FetchStatus.HTTP_ERROR, 500, None)
    assert local_site.paths() == ["/robots.txt", "/500", "/500"]


def test_not_found_is_not_retried(local_site: LocalSite, fetcher: HttpxPageFetcher) -> None:
    result = fetcher.fetch(local_site.url("/404"))
    assert (result.status, result.http_status) == (FetchStatus.HTTP_ERROR, 404)
    assert local_site.paths() == ["/robots.txt", "/404"]


def test_oversize_body_is_abandoned(local_site: LocalSite) -> None:
    local_site.big_bytes = 4096
    fetcher = HttpxPageFetcher(interval=FAST, timeout=SHORT_TIMEOUT, max_bytes=1024)
    try:
        result = fetcher.fetch(local_site.url("/big"))
        assert (result.status, result.body) == (FetchStatus.TOO_LARGE, None)
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    finally:
        fetcher.close()
    assert local_site.paths() == ["/robots.txt", "/big", "/page"]


def test_unresolvable_host_is_a_dns_error_without_any_request(fetcher: HttpxPageFetcher) -> None:
    result = fetcher.fetch("https://nonexistent.invalid/")
    assert (result.status, result.http_status, result.body) == (FetchStatus.DNS_ERROR, None, None)


def test_refused_connection_is_a_connection_error(local_site: LocalSite) -> None:
    url = local_site.url("/page")
    local_site.close()
    fetcher = HttpxPageFetcher(interval=FAST, timeout=SHORT_TIMEOUT)
    try:
        # The closed port refuses robots.txt twice (one retry), so the rules are unknown.
        assert fetcher.fetch(url).status is FetchStatus.ROBOTS_UNAVAILABLE
    finally:
        fetcher.close()


def test_extractor_drops_navigation_and_footer_but_keeps_the_body_and_title() -> None:
    html = PAGE_HTML.replace(b"{name}", b"Home")
    extracted = TrafilaturaExtractor().extract(html, "https://acme.example/")
    assert extracted.title == "Acme Care Home"
    assert "support coordination and daily personal activities" in extracted.text
    assert "About us" not in extracted.text
    assert "Privacy policy" not in extracted.text


def test_extractor_returns_empty_text_for_a_page_without_content() -> None:
    extracted = TrafilaturaExtractor().extract(b"<html><body></body></html>", "https://x.example/")
    assert extracted == extracted.__class__(None, "")
