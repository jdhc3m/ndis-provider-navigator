"""Pure page-collection rules: URL choice, fetch policy, retry class, pacing, crawl-delay
cap, media types, hashing."""

import hashlib

import pytest

from ndis.domain.entity_resolution import MatchStatus
from ndis.domain.pages import (
    MAX_CRAWL_DELAY_SECONDS,
    REQUEST_INTERVAL_SECONDS,
    FetchStatus,
    ProviderSite,
    choose_website,
    is_html_content_type,
    is_transient,
    normalise_text,
    normalise_url,
    pacing_delay,
    request_interval,
    should_fetch,
    text_hash,
    text_path,
    wants_evidence,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.acme.com.au", "https://www.acme.com.au/"),
        ("  HTTP://Acme.COM.AU/About#team  ", "http://acme.com.au/About"),
        ("acme.com.au/services", "https://acme.com.au/services"),
        (
            "https://acme.com.au/?utm_source=google&utm_medium=organic&page=2&gclid=abc",
            "https://acme.com.au/?page=2",
        ),
        ("https://acme.com.au:8443/x", "https://acme.com.au:8443/x"),
        ("https://acme.com.au:abc/", None),
        ("https://www. centralcoastneurorehab.com.au", None),
        ("https://18 mitchell street", None),
        ("https://eagle care disability services", None),
        ("mailto:info@acme.com.au", None),
        ("ftp://acme.com.au/file", None),
        ("localhost", None),
        ("", None),
        (None, None),
    ],
)
def test_normalise_url(raw: str | None, expected: str | None) -> None:
    assert normalise_url(raw) == expected


@pytest.mark.parametrize(
    ("register", "maps", "expected"),
    [
        ("https://acme.com.au", "https://acme.com.au/?utm_source=gbp", "https://acme.com.au/"),
        # The register wins even when Maps points somewhere else entirely.
        ("https://www.niacare.com.au", "https://babylonhs.com.au/", "https://www.niacare.com.au/"),
        # A mangled register website falls back to Maps.
        ("https://188 George Street", "http://acme.com.au/", "http://acme.com.au/"),
        (None, "acme.com.au", "https://acme.com.au/"),
        ("", None, None),
        (None, None, None),
    ],
)
def test_choose_website_prefers_the_register(
    register: str | None, maps: str | None, expected: str | None
) -> None:
    assert choose_website(register, maps) == expected


def test_provider_key_is_abn_when_linked_else_place_id() -> None:
    linked = ProviderSite("place-1", "10000000001", MatchStatus.REGISTERED, None, None)
    unlinked = ProviderSite("place-2", None, MatchStatus.UNREGISTERED, None, None)
    assert (linked.provider_key, unlinked.provider_key) == ("10000000001", "place-2")


@pytest.mark.parametrize(
    ("status", "wanted"),
    [
        (MatchStatus.REGISTERED, True),
        (MatchStatus.UNREGISTERED, True),
        (MatchStatus.AMBIGUOUS, True),
        (MatchStatus.REVOKED_OR_BANNED, True),
        (MatchStatus.NOT_A_PROVIDER, False),
    ],
)
def test_wants_evidence_excludes_only_non_providers(status: MatchStatus, wanted: bool) -> None:
    assert wants_evidence(status) is wanted


@pytest.mark.parametrize(
    ("has_cached_ok", "refresh", "expected"),
    [(False, False, True), (True, False, False), (True, True, True), (False, True, True)],
)
def test_should_fetch(has_cached_ok: bool, refresh: bool, expected: bool) -> None:
    assert should_fetch(has_cached_ok, refresh) is expected


@pytest.mark.parametrize(
    ("status", "http_status", "transient"),
    [
        (FetchStatus.TIMEOUT, None, True),
        (FetchStatus.CONNECTION_ERROR, None, True),
        (FetchStatus.HTTP_ERROR, 500, True),
        (FetchStatus.HTTP_ERROR, 503, True),
        (FetchStatus.HTTP_ERROR, 404, False),
        (FetchStatus.HTTP_ERROR, 403, False),
        (FetchStatus.HTTP_ERROR, 429, False),
        (FetchStatus.HTTP_ERROR, None, False),
        (FetchStatus.ROBOTS_DISALLOWED, None, False),
        (FetchStatus.ROBOTS_UNAVAILABLE, None, False),
        (FetchStatus.CRAWL_DELAY_TOO_LONG, None, False),
        (FetchStatus.TOO_LARGE, 200, False),
        (FetchStatus.EMPTY, 200, False),
        (FetchStatus.OK, 200, False),
    ],
)
def test_is_transient(status: FetchStatus, http_status: int | None, transient: bool) -> None:
    assert is_transient(status, http_status) is transient


@pytest.mark.parametrize(
    ("crawl_delay", "expected"),
    [
        (None, 1.0),
        (0.5, 1.0),
        (4.0, 4.0),
        (MAX_CRAWL_DELAY_SECONDS, MAX_CRAWL_DELAY_SECONDS),
        (MAX_CRAWL_DELAY_SECONDS + 1, None),
    ],
)
def test_request_interval_honours_crawl_delay_up_to_the_cap(
    crawl_delay: float | None, expected: float | None
) -> None:
    assert request_interval(1.0, crawl_delay) == expected


@pytest.mark.parametrize(
    ("content_type", "html"),
    [
        ("text/html; charset=utf-8", True),
        ("TEXT/HTML", True),
        ("application/xhtml+xml", True),
        (None, True),
        ("", True),
        ("application/pdf", False),
        ("text/plain", False),
        ("image/png", False),
        ("application/json; charset=utf-8", False),
    ],
)
def test_is_html_content_type(content_type: str | None, html: bool) -> None:
    assert is_html_content_type(content_type) is html


def test_pacing_delay_keeps_one_interval_between_requests() -> None:
    assert pacing_delay(None, 100.0, REQUEST_INTERVAL_SECONDS) == 0.0
    assert pacing_delay(100.0, 100.25, REQUEST_INTERVAL_SECONDS) == pytest.approx(0.75)
    assert pacing_delay(100.0, 101.0, REQUEST_INTERVAL_SECONDS) == 0.0
    assert pacing_delay(100.0, 105.0, REQUEST_INTERVAL_SECONDS) == 0.0
    assert pacing_delay(100.0, 100.1, 5.0) == pytest.approx(4.9)


def test_normalise_text_trims_lines_and_collapses_blank_runs() -> None:
    raw = "\n\nAlpha Therapy  \r\n\r\n\r\nWest End\t\n   \nBrisbane\n\n\n"
    assert normalise_text(raw) == "Alpha Therapy\n\nWest End\n\nBrisbane"
    assert normalise_text("   \n \n") == ""


def test_text_hash_is_sha256_of_normalised_text() -> None:
    assert text_hash("Alpha  \n\n\nBeta\n") == text_hash("Alpha\n\nBeta")
    assert text_hash("Alpha\n\nBeta") == hashlib.sha256(b"Alpha\n\nBeta").hexdigest()
    assert text_hash("Alpha") != text_hash("Beta")


def test_text_path_is_a_short_url_digest() -> None:
    path = text_path("https://acme.com.au/")
    assert path == hashlib.sha256(b"https://acme.com.au/").hexdigest()[:16] + ".txt"
    assert path != text_path("https://acme.com.au/about")
    assert "/" not in path
