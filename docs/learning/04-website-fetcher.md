# 04 — Website fetcher

## What we built

The evidence collector. For every Maps place linked in ticket 03 (except `not_a_provider`), `scripts/collect_pages.py` picks a website — the register's `Website` column first, the Maps URL second — and makes sure we hold cleaned page text for it under `data/private/pages/`, with a public-safe `page_meta` row (url, fetched_at, status, text hash, source). It seeds from the 1,983 `ok` pages of the existing crawl first, so 288 providers were covered without a single request, and fetches live only for the 46 providers that had no cached page. Live fetching is deliberately polite: `robots.txt` per host, one request per second per host, 10 s timeouts, one retry on transient errors only, an identifying `User-Agent`, a 2 MB cap, and redirects followed by hand so the destination host's robots rules are checked too.

## Concepts

- **Polite HTTP:** read `robots.txt` before any page, identify yourself, pace per host, never retry a 4xx. `HttpxPageFetcher` in `src/ndis/adapters/web_pages.py`; the rules it follows are RFC 9309.
- **Robots policy when robots.txt is unreachable:** 4xx means "no rules, allowed"; 5xx, timeout or connection failure means "rules unknown, do not fetch" (`FetchStatus.ROBOTS_UNAVAILABLE`). The conservative reading of RFC 9309 §2.3.1.4. `_rules_for` in `web_pages.py`.
- **Rate limiting with an injected clock:** `pacing_delay(last, now, interval)` in `src/ndis/domain/pages.py` is pure; the adapter supplies `time.monotonic` and `time.sleep` by default and tests inject a fake clock, so a 1 s pacing test runs in milliseconds and cannot flake. Kotlin analogy: injecting a `Clock` into a `RateLimiter` instead of calling `System.nanoTime()` inside it.
- **Transient vs permanent failure:** `is_transient(status, http_status)` says timeout, connection error and 5xx get one retry; 4xx (including 429), robots decisions and oversize bodies do not. `domain/pages.py`.
- **Content hashing:** `text_hash()` is the sha256 of whitespace-normalised text; the file name is `sha256(url)[:16].txt`. Two listings that share a URL share one file, and a re-fetch with identical text produces the same hash so change detection is free. `domain/pages.py`, `FilePageStore` in `adapters/sqlite_repository.py`.
- **Cache-first design:** `should_fetch(has_cached_ok, refresh)` is the only rule that decides whether to touch the network. Seeding skips `(provider_key, url)` pairs already stored, so a `--refresh` result is never overwritten by the next plain run. `CollectPages` in `src/ndis/application/pages.py`.
- **Integration fixture:** `LocalSite` in `tests/conftest.py` is a real `ThreadingHTTPServer` on a random port with a request log; tests assert on which paths the server saw, not on the fetcher's own claim. Equivalent to `MockWebServer` in OkHttp tests.

## Decisions and why

- **Seed from the old crawl before fetching anything.** 2,266 pages already existed; re-crawling 360 sites would have been impolite and slow. Only `status = ok` rows are imported; blank-text rows are stored as `empty` so those providers are fetched live.
- **Follow redirects by hand instead of `follow_redirects=True`.** The Reviewer showed that a site redirecting to `facebook.com/...` would have been fetched even though a direct request to that host is `robots_disallowed`. Each hop now re-runs resolve → robots → pacing; max 5 hops. Alternative rejected: let httpx follow and check robots only on the first host — simpler, but it breaks the policy exactly where it matters.
- **Seeding never overwrites a stored page.** The first version re-seeded on every run and clobbered live results: a `--dry-run` silently moved 14 providers back into the fetch queue. `PageStore.stored_pages()` makes seeding idempotent. Alternative rejected: "newest `fetched_at` wins" inside `save()`, which hides the rule in the adapter.
- **No local DNS pre-flight.** The first version resolved hosts with `getaddrinfo` before fetching, which disagrees with the corporate proxy's resolver and was never retried. Connection failures are now one `connection_error` class with one retry; a separate `dns_error` status was dropped because httpx cannot distinguish it.
- **Deterministic pacing test.** The real-sleep test measured server-side arrival gaps against a client-side 1.0 s target; 2 of 80 gaps were 0.998 s on an idle Mac. Timing assertions on a shared CI runner are a flake waiting to happen; the test now uses the injected clock and the server's request order.
- **`Crawl-delay` honoured but capped at 30 s.** A site asking for 3600 s gets `crawl_delay_too_long` and no request rather than an hour of sleep per page.
- **`--dry-run` writes nothing.** It lists seeds and planned fetches; the first version still wrote 2,000 rows during a dry run.
- **The private pages directory is the only place page text lives.** `page_meta` has no text column (asserted in a test); the pre-commit guard and `git ls-files data/` keep it out of history.

## How to say it in an interview

"Provider websites are the only source for what services a provider offers, so I built a fetcher whose first rule is not to be a nuisance. It seeds from an existing crawl, so 288 of 334 sites never got a second request; for the rest it reads robots.txt per host, paces at one request per second with an injected clock, retries once on timeouts and 5xx only, identifies itself, caps bodies at 2 MB, and follows redirects by hand so the destination host's robots rules apply too — the independent reviewer caught that a plain `follow_redirects=True` would have walked straight past a `Disallow`. The pacing and robots logic are pure functions in the domain layer, the HTTP client is an adapter behind a port, and the integration tests run against a real local HTTP server and assert on the request log the server saw. Page text is stored privately and hashed; the public table carries only URL, time, status and hash. The honest result on a corporate-proxied laptop is that 23 of the 46 live fetches came back as proxy block pages — the counts say so rather than pretending."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: what does the fetcher do, and what does "polite" mean here?
2. Why does the fetcher seed from the existing Maps crawl before touching the network?

**Spot it**

3. Which pure function is the only rule that decides whether a provider's page is fetched live?
4. Which test fixture stands in for a real website, and what does it let tests assert that a mock could not?

**Defend it**

5. A provider's site answers `robots.txt` with HTTP 503. Does the fetcher request the page? Why?
6. Why does the pacing test inject a fake clock instead of sleeping for real, and what does it still assert against the local server?
7. After `--refresh` re-fetched a provider's page, the next plain run of `collect_pages.py` is started. What happens to that page?

## Numbers

- 411 tests (103 new), inner-layer coverage 99.52%, import-linter contracts 2 kept; suite 7.1–7.5 s with no real-time sleeps.
- 415 links → 354 providers (not_a_provider excluded, one key per ABN) → 334 with a usable website.
- Seeded 1,875 `ok` + 26 `empty` pages for 305 providers from the crawl; 1,824 text files, 16 MB, private. 288 providers had a cached `ok` page and were never fetched.
- Live gap 46 providers, 105 s wall: ok 4, empty 1, robots_disallowed 2, robots_unavailable 20, http_error 23 (16 × 403 proxy block pages, 4 × 404, 2 × 500, 1 × 429). Those 46 will be retried automatically from an unfiltered network.
- 2 review rounds: round 1 had 3 must-fix (flaky timing test, re-seeding clobbered live rows, redirects bypassed robots) and 8 nice-to-haves; round 2 had 0 must-fix.
- CI `quality` job: 25 s on the green run <https://github.com/jdhc3m/ndis-provider-navigator/actions/runs/42552482736>. PR: <https://github.com/jdhc3m/ndis-provider-navigator/pull/26>.

---

Answers: 1. It downloads each provider's website once, strips boilerplate and stores cleaned text under `data/private/pages/` with a public-safe `page_meta` row. Polite: read `robots.txt` first, identify yourself, one request per second per host, never retry a 4xx. 2. About 392 sites were already crawled in the Maps export; importing those as cached pages covers ~290 providers without re-hitting anyone's server, so live fetches happen only for providers with no cached `ok` page or when `--refresh` is passed. 3. `should_fetch(has_cached_ok, refresh)` in `src/ndis/domain/pages.py`; `CollectPages` in `application/pages.py` calls it and nothing else decides. 4. `LocalSite` in `tests/conftest.py`, a real `ThreadingHTTPServer` with a request log; tests assert which paths the server actually saw and in which order (`/robots.txt` before `/page`), not what the fetcher claims it did — like OkHttp's `MockWebServer`. 5. No. 5xx means the rules are unknown, so the result is `robots_unavailable` and no page request is made (after one retry of the robots fetch itself); only a 4xx on `robots.txt` means "no rules, allowed". 6. Real sleeps measured server-side had jitter on both sides of 1.0 s, so the assertion flaked on an idle Mac and would flake more on a shared CI runner; the test now asserts the fetcher asked its clock to wait exactly `[1.0, 1.0]` seconds and that the server saw `/robots.txt`, `/page`, `/other` in that order. 7. Nothing: seeding skips `(provider_key, url)` pairs the store already holds, the provider has a cached `ok` page, so it is counted as `skipped_cached` and the live row and its text file survive.
