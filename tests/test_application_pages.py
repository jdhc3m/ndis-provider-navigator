"""CollectPages against fake ports: seeding, fetch policy, precedence, extraction, counts."""

from datetime import UTC, datetime

from ndis.application.pages import CollectPages
from ndis.domain.entity_resolution import MatchStatus
from ndis.domain.pages import (
    CachedPage,
    ExtractedText,
    FetchResult,
    FetchStatus,
    PageMeta,
    PageSource,
    ProviderSite,
    text_hash,
    text_path,
)

NOW = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
CRAWLED_AT = datetime(2026, 11, 7, 3, 49, 42, tzinfo=UTC)
HTML = b"<html><body><p>Hello from the live site.</p></body></html>"


class FakeSites:
    def __init__(self, sites: list[ProviderSite]) -> None:
        self._sites = sites

    def sites(self) -> tuple[ProviderSite, ...]:
        return tuple(self._sites)


class FakeCached:
    def __init__(self, pages: list[CachedPage]) -> None:
        self._pages = pages

    def ok_pages(self) -> tuple[CachedPage, ...]:
        return tuple(self._pages)


class FakeFetcher:
    """Answers from a table of URL → result and records every call."""

    def __init__(self, results: dict[str, FetchResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self.results.get(url, FetchResult(url, FetchStatus.OK, 200, HTML))


class FakeExtractor:
    def __init__(self, text: str = "Hello from the live site.") -> None:
        self.text = text
        self.calls: list[tuple[bytes, str]] = []

    def extract(self, body: bytes, url: str) -> ExtractedText:
        self.calls.append((body, url))
        return ExtractedText("Live title", self.text)


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[tuple[PageMeta, str | None]] = []

    def save(self, meta: PageMeta, text: str | None) -> None:
        self.saved.append((meta, text))

    def providers_with_ok_pages(self) -> frozenset[str]:
        return frozenset(m.provider_key for m, _ in self.saved if m.status is FetchStatus.OK)

    def stored_pages(self) -> frozenset[tuple[str, str]]:
        return frozenset((m.provider_key, m.url) for m, _ in self.saved)

    def metas(self) -> list[PageMeta]:
        return [meta for meta, _ in self.saved]

    def current(self) -> dict[tuple[str, str], tuple[PageSource, str | None]]:
        """What a real upserting store would hold: the last save per (provider_key, url)."""
        return {(m.provider_key, m.url): (m.source, text) for m, text in self.saved}


ALPHA = ProviderSite(
    "place-alpha",
    "10000000001",
    MatchStatus.REGISTERED,
    "https://www.alpha.example/",
    "https://alpha.example/?utm_source=gbp",
)
# A second Maps listing of the same registered provider.
ALPHA_TWO = ProviderSite("place-alpha-2", "10000000001", MatchStatus.REGISTERED, None, None)
BETA = ProviderSite("place-beta", None, MatchStatus.UNREGISTERED, None, "beta.example")
GAMMA = ProviderSite(
    "place-gamma", "10000000003", MatchStatus.REVOKED_OR_BANNED, "https://188 George Street", None
)
HIRE = ProviderSite(
    "place-recruit", None, MatchStatus.NOT_A_PROVIDER, None, "https://hireco.example/"
)
CACHED = [
    CachedPage("place-alpha", "https://www.alpha.example/", "Alpha", "Alpha home.\n\n", CRAWLED_AT),
    CachedPage("place-alpha", "https://www.alpha.example/about", None, "  \n", CRAWLED_AT),
    CachedPage("place-recruit", "https://hireco.example/", "Hire", "Jobs.", CRAWLED_AT),
    CachedPage("place-nobody", "https://nobody.example/", None, "Not a target.", CRAWLED_AT),
]


def collect(
    sites: list[ProviderSite],
    cached: list[CachedPage],
    fetcher: FakeFetcher | None = None,
    extractor: FakeExtractor | None = None,
    store: FakeStore | None = None,
) -> tuple[CollectPages, FakeFetcher, FakeStore]:
    fetcher = fetcher or FakeFetcher()
    store = store or FakeStore()
    use_case = CollectPages(
        FakeSites(sites),
        FakeCached(cached),
        fetcher,
        extractor or FakeExtractor(),
        store,
        clock=lambda: NOW,
    )
    return use_case, fetcher, store


def test_seeds_crawl_pages_of_target_providers_only() -> None:
    use_case, fetcher, store = collect([ALPHA, ALPHA_TWO, BETA, GAMMA, HIRE], CACHED)
    result = use_case()
    seeded = [m for m in store.metas() if m.source is PageSource.MAPS_CRAWL]
    assert [(m.provider_key, m.url, m.status) for m in seeded] == [
        ("10000000001", "https://www.alpha.example/", FetchStatus.OK),
        ("10000000001", "https://www.alpha.example/about", FetchStatus.EMPTY),
    ]
    home, about = seeded
    assert home == PageMeta(
        url="https://www.alpha.example/",
        provider_key="10000000001",
        fetched_at=CRAWLED_AT,
        status=FetchStatus.OK,
        source=PageSource.MAPS_CRAWL,
        text_hash=text_hash("Alpha home."),
        text_path=text_path("https://www.alpha.example/"),
        title="Alpha",
    )
    assert store.saved[0][1] == "Alpha home."
    assert (about.text_hash, about.text_path, store.saved[1][1]) == (None, None, None)
    assert result.seeded_pages == {FetchStatus.OK: 1, FetchStatus.EMPTY: 1}
    assert (result.seeded_providers, result.seeded_skipped) == (1, 0)
    # Alpha is cached, Beta is fetched, Gamma has no usable website, Hire is not a provider.
    assert fetcher.calls == ["https://beta.example/"]
    assert (result.providers, result.with_website, result.skipped_cached) == (3, 2, 1)
    assert result.fetched == {FetchStatus.OK: 1}


def test_cached_provider_is_fetched_again_only_with_refresh() -> None:
    use_case, fetcher, _ = collect([ALPHA, ALPHA_TWO], CACHED)
    assert use_case().skipped_cached == 1
    assert fetcher.calls == []
    result = use_case(refresh=True)
    assert fetcher.calls == ["https://www.alpha.example/"]
    assert (result.skipped_cached, result.fetched) == (0, {FetchStatus.OK: 1})


def test_seeding_never_overwrites_a_stored_page_so_refresh_results_survive() -> None:
    use_case, fetcher, store = collect([ALPHA, ALPHA_TWO], CACHED)
    use_case()
    use_case(refresh=True)
    live = store.current()[("10000000001", "https://www.alpha.example/")]
    assert live == (PageSource.LIVE, "Hello from the live site.")
    again = use_case()
    assert store.current()[("10000000001", "https://www.alpha.example/")] == live
    assert fetcher.calls == ["https://www.alpha.example/"]
    assert (again.seeded_pages, again.seeded_providers, again.seeded_skipped) == ({}, 0, 2)
    assert again.skipped_cached == 1


def test_two_crawl_rows_for_one_page_store_it_once() -> None:
    twice = [CACHED[0], CachedPage("place-alpha-2", CACHED[0].url, None, "Again.", CRAWLED_AT)]
    use_case, _, store = collect([ALPHA, ALPHA_TWO], twice)
    result = use_case()
    assert [(m.url, text) for m, text in store.saved] == [(CACHED[0].url, "Alpha home.")]
    assert (result.seeded_pages, result.seeded_skipped) == ({FetchStatus.OK: 1}, 1)


def test_live_fetch_uses_register_website_before_maps_and_stores_cleaned_text() -> None:
    extractor = FakeExtractor("  Hello from the live site.  \n\n\n")
    use_case, fetcher, store = collect([ALPHA, BETA], [], extractor=extractor)
    result = use_case()
    assert fetcher.calls == ["https://www.alpha.example/", "https://beta.example/"]
    assert extractor.calls[0] == (HTML, "https://www.alpha.example/")
    alpha_meta, alpha_text = store.saved[0]
    assert alpha_text == "Hello from the live site."
    assert alpha_meta == PageMeta(
        url="https://www.alpha.example/",
        provider_key="10000000001",
        fetched_at=NOW,
        status=FetchStatus.OK,
        source=PageSource.LIVE,
        text_hash=text_hash("Hello from the live site."),
        text_path=text_path("https://www.alpha.example/"),
        title="Live title",
    )
    assert store.saved[1][0].provider_key == "place-beta"
    assert result.fetched == {FetchStatus.OK: 2}


def test_failures_and_empty_extractions_are_recorded_without_text() -> None:
    failures = {
        "https://www.alpha.example/": FetchResult(
            "https://www.alpha.example/", FetchStatus.ROBOTS_DISALLOWED
        ),
        "https://beta.example/": FetchResult("https://beta.example/", FetchStatus.HTTP_ERROR, 503),
    }
    delta = ProviderSite("place-delta", None, MatchStatus.AMBIGUOUS, None, "https://delta.example")
    use_case, _, store = collect(
        [ALPHA, BETA, delta], [], fetcher=FakeFetcher(failures), extractor=FakeExtractor("")
    )
    result = use_case()
    assert [(m.status, m.text_hash, m.text_path, text) for m, text in store.saved] == [
        (FetchStatus.ROBOTS_DISALLOWED, None, None, None),
        (FetchStatus.HTTP_ERROR, None, None, None),
        (FetchStatus.EMPTY, None, None, None),
    ]
    assert all(m.source is PageSource.LIVE and m.fetched_at == NOW for m in store.metas())
    assert result.fetched == {
        FetchStatus.EMPTY: 1,
        FetchStatus.ROBOTS_DISALLOWED: 1,
        FetchStatus.HTTP_ERROR: 1,
    }
    assert store.providers_with_ok_pages() == frozenset()


def test_limit_caps_live_fetches_and_still_counts_the_rest() -> None:
    delta = ProviderSite("place-delta", None, MatchStatus.AMBIGUOUS, None, "https://delta.example")
    use_case, fetcher, store = collect([ALPHA, BETA, delta], [])
    limited = use_case(limit=2)
    assert fetcher.calls == ["https://www.alpha.example/", "https://beta.example/"]
    assert (sum(limited.fetched.values()), limited.over_limit, limited.skipped_cached) == (2, 1, 0)
    # Alpha and Beta now have ok pages; a limit of 1 fetches Delta and counts both as cached.
    second = use_case(limit=1)
    assert fetcher.calls[-1] == "https://delta.example/"
    assert (sum(second.fetched.values()), second.over_limit, second.skipped_cached) == (1, 0, 2)
    assert len(store.saved) == 3


def test_dry_run_writes_nothing_and_only_counts_and_plans() -> None:
    delta = ProviderSite("place-delta", None, MatchStatus.AMBIGUOUS, None, "https://delta.example")
    use_case, fetcher, store = collect([ALPHA, ALPHA_TWO, BETA, delta], CACHED)
    planned = use_case(dry_run=True)
    assert (fetcher.calls, store.saved) == ([], [])
    # The seeding is counted as it would run, and Alpha's would-be ok page keeps it cached.
    assert planned.seeded_pages == {FetchStatus.OK: 1, FetchStatus.EMPTY: 1}
    assert (planned.seeded_providers, planned.skipped_cached) == (1, 1)
    assert planned.planned == ("https://beta.example/", "https://delta.example/")
    assert planned.fetched == {}
    capped = use_case(dry_run=True, limit=1)
    assert (capped.planned, capped.over_limit) == (("https://beta.example/",), 1)
    assert store.saved == []


def test_summary_lists_every_count() -> None:
    use_case, _, _ = collect([ALPHA, ALPHA_TWO, BETA, GAMMA, HIRE], CACHED)
    assert use_case().summary() == (
        "3 providers, 2 with a website; seeded 2 pages for 1 providers (ok=1, empty=1), "
        "0 crawl pages already stored; skipped 1 already cached; fetched 1 live (ok=1); "
        "planned 0; 0 over the limit"
    )
    # Beta's live page is now stored too, so the second run has nothing left to fetch.
    assert use_case(refresh=True, dry_run=True, limit=1).summary() == (
        "3 providers, 2 with a website; seeded 0 pages for 0 providers (), "
        "2 crawl pages already stored; skipped 0 already cached; fetched 0 live (); "
        "planned 1; 1 over the limit"
    )
