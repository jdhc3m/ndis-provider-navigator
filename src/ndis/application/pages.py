"""Ports and the use case that collects website evidence for linked providers."""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from ndis.domain.pages import (
    CachedPage,
    ExtractedText,
    FetchResult,
    FetchStatus,
    PageMeta,
    PageSource,
    ProviderSite,
    choose_website,
    normalise_text,
    should_fetch,
    text_hash,
    text_path,
    wants_evidence,
)


class ProviderSiteSource(Protocol):
    """Read port for ``provider_links`` joined to the register's website column."""

    def sites(self) -> tuple[ProviderSite, ...]:
        """Every link with its register and Maps websites, ordered by place id."""
        ...


class CachedPageSource(Protocol):
    """Read port for the earlier Maps crawl; yields only its ``ok`` pages."""

    def ok_pages(self) -> tuple[CachedPage, ...]:
        """Every ``ok`` page of the crawl, ordered by place id then URL."""
        ...


class PageFetcher(Protocol):
    """Fetch one URL politely: robots.txt, per-host pacing, timeout and one retry."""

    def fetch(self, url: str) -> FetchResult:
        """The body when the site allowed it and answered, otherwise the failure status."""
        ...


class TextExtractor(Protocol):
    """Turn an HTML body into boilerplate-free text."""

    def extract(self, body: bytes, url: str) -> ExtractedText:
        """Cleaned text (possibly empty) and the page title when one was found."""
        ...


class PageStore(Protocol):
    """Persistence port: private text files plus the public-safe ``page_meta`` rows."""

    def save(self, meta: PageMeta, text: str | None) -> None:
        """Upsert the metadata row for ``(provider_key, url)`` and write ``text`` privately."""
        ...

    def providers_with_ok_pages(self) -> frozenset[str]:
        """Provider keys that already have at least one ``ok`` page stored."""
        ...

    def stored_pages(self) -> frozenset[tuple[str, str]]:
        """Every ``(provider_key, url)`` pair that already has a metadata row."""
        ...


@dataclass(frozen=True)
class CollectionResult:
    """Counts the script logs and the learning note records. ``seeded_pages`` counts crawl
    rows stored this run (or that a dry run would store) and ``seeded_skipped`` the crawl
    rows whose ``(provider_key, url)`` the store already held; ``planned`` lists the URLs a
    dry run would have fetched and ``over_limit`` the fetches ``limit`` left for later."""

    providers: int
    with_website: int
    seeded_pages: Mapping[FetchStatus, int]
    seeded_providers: int
    seeded_skipped: int
    skipped_cached: int
    fetched: Mapping[FetchStatus, int]
    planned: tuple[str, ...] = field(default=())
    over_limit: int = 0

    def summary(self) -> str:
        """One log line with every count."""
        seeded = ", ".join(f"{s.value}={n}" for s, n in self.seeded_pages.items())
        fetched = ", ".join(f"{s.value}={n}" for s, n in self.fetched.items())
        return (
            f"{self.providers} providers, {self.with_website} with a website; "
            f"seeded {sum(self.seeded_pages.values())} pages for {self.seeded_providers} "
            f"providers ({seeded}), {self.seeded_skipped} crawl pages already stored; "
            f"skipped {self.skipped_cached} already cached; "
            f"fetched {sum(self.fetched.values())} live ({fetched}); "
            f"planned {len(self.planned)}; {self.over_limit} over the limit"
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CollectPages:
    """Use case: seed the page cache from the Maps crawl, then fetch the gap live.

    Providers are the ``provider_links`` rows the matcher did not call ``not_a_provider``,
    keyed by ABN when linked and by place id otherwise. Seeding stores every ``ok`` crawl
    page of those providers (as ``empty`` when its text is blank) whose ``(provider_key,
    url)`` the store does not hold yet, so a crawl row never overwrites a later live result.
    A live fetch then goes to the chosen website of each provider that has no ``ok`` page
    stored, or of every provider with ``refresh``; ``limit`` caps the live fetches and
    ``dry_run`` writes nothing and only counts the seeding and lists the fetches.
    """

    def __init__(
        self,
        sites: ProviderSiteSource,
        cached: CachedPageSource,
        fetcher: PageFetcher,
        extractor: TextExtractor,
        store: PageStore,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sites = sites
        self._cached = cached
        self._fetcher = fetcher
        self._extractor = extractor
        self._store = store
        self._clock = clock

    def __call__(
        self, *, refresh: bool = False, limit: int | None = None, dry_run: bool = False
    ) -> CollectionResult:
        websites: dict[str, str | None] = {}
        key_of_place: dict[str, str] = {}
        for site in self._sites.sites():
            if not wants_evidence(site.match_status):
                continue
            key_of_place[site.place_id] = site.provider_key
            chosen = choose_website(site.register_website, site.maps_website)
            if websites.get(site.provider_key) is None:
                websites[site.provider_key] = chosen

        seeded: Counter[FetchStatus] = Counter()
        seeded_providers: set[str] = set()
        seeded_skipped = 0
        stored = set(self._store.stored_pages())
        cached_ok = set(self._store.providers_with_ok_pages())
        for page in self._cached.ok_pages():
            key = key_of_place.get(page.place_id)
            if key is None:
                continue
            if (key, page.url) in stored:
                seeded_skipped += 1
                continue
            stored.add((key, page.url))
            status = self._seed(page, key, dry_run)
            seeded[status] += 1
            seeded_providers.add(key)
            if status is FetchStatus.OK:
                cached_ok.add(key)

        fetched: Counter[FetchStatus] = Counter()
        planned: list[str] = []
        skipped_cached = 0
        over_limit = 0
        for key, url in websites.items():
            if url is None:
                continue
            if not should_fetch(key in cached_ok, refresh):
                skipped_cached += 1
                continue
            if limit is not None and len(planned) + sum(fetched.values()) >= limit:
                over_limit += 1
                continue
            if dry_run:
                planned.append(url)
                continue
            fetched[self._fetch(url, key)] += 1

        return CollectionResult(
            providers=len(websites),
            with_website=sum(url is not None for url in websites.values()),
            seeded_pages={status: seeded[status] for status in FetchStatus if seeded[status]},
            seeded_providers=len(seeded_providers),
            seeded_skipped=seeded_skipped,
            skipped_cached=skipped_cached,
            fetched={status: fetched[status] for status in FetchStatus if fetched[status]},
            planned=tuple(planned),
            over_limit=over_limit,
        )

    def _seed(self, page: CachedPage, key: str, dry_run: bool) -> FetchStatus:
        text = normalise_text(page.text)
        status = FetchStatus.OK if text else FetchStatus.EMPTY
        if dry_run:
            return status
        self._store.save(
            PageMeta(
                url=page.url,
                provider_key=key,
                fetched_at=page.fetched_at,
                status=status,
                source=PageSource.MAPS_CRAWL,
                text_hash=text_hash(text) if text else None,
                text_path=text_path(page.url) if text else None,
                title=page.title,
            ),
            text or None,
        )
        return status

    def _fetch(self, url: str, key: str) -> FetchStatus:
        result = self._fetcher.fetch(url)
        fetched_at = self._clock()
        if result.status is not FetchStatus.OK or result.body is None:
            self._store.save(PageMeta(url, key, fetched_at, result.status, PageSource.LIVE), None)
            return result.status
        extracted = self._extractor.extract(result.body, url)
        text = normalise_text(extracted.text)
        status = FetchStatus.OK if text else FetchStatus.EMPTY
        self._store.save(
            PageMeta(
                url=url,
                provider_key=key,
                fetched_at=fetched_at,
                status=status,
                source=PageSource.LIVE,
                text_hash=text_hash(text) if text else None,
                text_path=text_path(url) if text else None,
                title=extracted.title,
            ),
            text or None,
        )
        return status
