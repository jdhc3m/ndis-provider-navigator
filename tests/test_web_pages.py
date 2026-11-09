"""The httpx fetcher against a real local HTTP server: robots.txt, per-host pacing, timeout
and retry, size cap, redirects, non-HTML answers and the identifying User-Agent; the
trafilatura extractor on HTML with navigation and footer boilerplate."""

from collections.abc import Iterator

import pytest

from ndis.adapters.web_pages import (
    MAX_REDIRECTS,
    ROBOTS_TOKEN,
    USER_AGENT,
    HttpxPageFetcher,
    TrafilaturaExtractor,
)
from ndis.domain.pages import (
    MAX_CRAWL_DELAY_SECONDS,
    REQUEST_INTERVAL_SECONDS,
    ExtractedText,
    FetchStatus,
)
from tests.conftest import PAGE_HTML, LocalSite

FAST = 0.01
SHORT_TIMEOUT = 0.3


@pytest.fixture
def fetcher() -> Iterator[HttpxPageFetcher]:
    fetcher = HttpxPageFetcher(interval=FAST, timeout=SHORT_TIMEOUT)
    yield fetcher
    fetcher.close()


class FakeTime:
    """A clock that only moves when the fetcher sleeps, recording every wait."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


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
    fake = FakeTime()
    fetcher = HttpxPageFetcher(timeout=SHORT_TIMEOUT, clock=fake.clock, sleep=fake.sleep)
    try:
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
        assert fetcher.fetch(local_site.url("/other")).status is FetchStatus.OK
    finally:
        fetcher.close()
    # robots.txt, /page and /other share the host: two waits of the full default interval.
    assert fake.waits == [REQUEST_INTERVAL_SECONDS, REQUEST_INTERVAL_SECONDS]
    assert local_site.paths() == ["/robots.txt", "/page", "/other"]


def test_pacing_is_per_host_and_shared_across_calls(local_site: LocalSite) -> None:
    fake = FakeTime()
    fetcher = HttpxPageFetcher(
        interval=2.5, timeout=SHORT_TIMEOUT, clock=fake.clock, sleep=fake.sleep
    )
    try:
        fetcher.fetch(local_site.url("/page"))
        fetcher.fetch(local_site.url("/other"))
        fetcher.fetch("http://localhost:9/page")
    finally:
        fetcher.close()
    # The unbound port is another host, so its robots.txt attempt waits for nothing; its
    # retry is the only other wait.
    assert fake.waits == [2.5, 2.5, 2.5]
    assert local_site.paths() == ["/robots.txt", "/page", "/other"]


def test_crawl_delay_longer_than_our_interval_is_honoured(local_site: LocalSite) -> None:
    fake = FakeTime(0.0)
    local_site.robots = f"User-agent: {ROBOTS_TOKEN}\nCrawl-delay: 4\n"
    fetcher = HttpxPageFetcher(
        interval=1.0, timeout=SHORT_TIMEOUT, clock=fake.clock, sleep=fake.sleep
    )
    try:
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    finally:
        fetcher.close()
    assert fake.waits == [4.0]


def test_crawl_delay_over_the_cap_skips_the_site_without_sleeping(local_site: LocalSite) -> None:
    fake = FakeTime(0.0)
    delay = int(MAX_CRAWL_DELAY_SECONDS) + 1
    local_site.robots = f"User-agent: {ROBOTS_TOKEN}\nCrawl-delay: {delay}\n"
    fetcher = HttpxPageFetcher(
        interval=1.0, timeout=SHORT_TIMEOUT, clock=fake.clock, sleep=fake.sleep
    )
    try:
        result = fetcher.fetch(local_site.url("/page"))
    finally:
        fetcher.close()
    assert (result.status, result.body) == (FetchStatus.CRAWL_DELAY_TOO_LONG, None)
    assert fake.waits == []
    assert local_site.paths() == ["/robots.txt"]


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


def test_dropped_connection_is_retried_once_then_reported(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    result = fetcher.fetch(local_site.url("/drop"))
    assert (result.status, result.http_status, result.body) == (
        FetchStatus.CONNECTION_ERROR,
        None,
        None,
    )
    assert local_site.paths() == ["/robots.txt", "/drop", "/drop"]


def test_refused_connection_for_robots_blocks_the_host(local_site: LocalSite) -> None:
    url = local_site.url("/page")
    local_site.close()
    fetcher = HttpxPageFetcher(interval=FAST, timeout=SHORT_TIMEOUT)
    try:
        # The closed port refuses robots.txt twice (one retry), so the rules are unknown.
        assert fetcher.fetch(url).status is FetchStatus.ROBOTS_UNAVAILABLE
    finally:
        fetcher.close()


def test_non_html_answer_is_empty_without_reading_the_body(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    result = fetcher.fetch(local_site.url("/pdf"))
    assert (result.status, result.http_status, result.body) == (FetchStatus.EMPTY, 200, None)
    assert local_site.paths() == ["/robots.txt", "/pdf"]


def test_same_host_redirect_is_followed_and_paced(local_site: LocalSite) -> None:
    fake = FakeTime()
    fetcher = HttpxPageFetcher(timeout=SHORT_TIMEOUT, clock=fake.clock, sleep=fake.sleep)
    try:
        result = fetcher.fetch(local_site.url("/redirect"))
    finally:
        fetcher.close()
    assert (result.url, result.status, result.http_status) == (
        local_site.url("/page"),
        FetchStatus.OK,
        200,
    )
    assert local_site.paths() == ["/robots.txt", "/redirect", "/page"]
    assert fake.waits == [REQUEST_INTERVAL_SECONDS, REQUEST_INTERVAL_SECONDS]


def test_cross_host_redirect_consults_the_target_robots(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    target = LocalSite()
    try:
        target.robots = "User-agent: *\nDisallow: /\n"
        local_site.redirect_to = target.url("/secret")
        result = fetcher.fetch(local_site.url("/redirect"))
    finally:
        target.close()
    assert (result.url, result.status) == (target.url("/secret"), FetchStatus.ROBOTS_DISALLOWED)
    assert local_site.paths() == ["/robots.txt", "/redirect"]
    assert target.paths() == ["/robots.txt"]


def test_redirect_loop_ends_as_an_http_error_after_the_hop_limit(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    result = fetcher.fetch(local_site.url("/loop"))
    assert (result.status, result.http_status, result.body) == (FetchStatus.HTTP_ERROR, 302, None)
    assert local_site.paths() == ["/robots.txt"] + ["/loop"] * (MAX_REDIRECTS + 1)


def test_redirect_without_location_is_an_http_error(
    local_site: LocalSite, fetcher: HttpxPageFetcher
) -> None:
    local_site.redirect_to = None
    result = fetcher.fetch(local_site.url("/redirect"))
    assert (result.status, result.http_status) == (FetchStatus.HTTP_ERROR, 302)
    assert local_site.paths() == ["/robots.txt", "/redirect"]


def test_robots_redirect_is_followed(local_site: LocalSite, fetcher: HttpxPageFetcher) -> None:
    rules = LocalSite()
    try:
        rules.robots = f"User-agent: {ROBOTS_TOKEN}\nDisallow: /private\n"
        local_site.robots_redirect_to = rules.url("/robots.txt")
        assert (
            fetcher.fetch(local_site.url("/private/secret")).status is FetchStatus.ROBOTS_DISALLOWED
        )
        assert fetcher.fetch(local_site.url("/page")).status is FetchStatus.OK
    finally:
        rules.close()
    assert local_site.paths() == ["/robots.txt", "/page"]
    assert rules.paths() == ["/robots.txt"]


def test_extractor_drops_navigation_and_footer_but_keeps_the_body_and_title() -> None:
    html = PAGE_HTML.replace(b"{name}", b"Home")
    extracted = TrafilaturaExtractor().extract(html, "https://acme.example/")
    assert extracted.title == "Acme Care Home"
    assert "support coordination and daily personal activities" in extracted.text
    assert "About us" not in extracted.text
    assert "Privacy policy" not in extracted.text


def test_extractor_returns_empty_text_for_a_page_without_content() -> None:
    extracted = TrafilaturaExtractor().extract(b"<html><body></body></html>", "https://x.example/")
    assert extracted == ExtractedText(None, "")
