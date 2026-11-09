# NDIS Provider Navigator

A portfolio project for grounded NDIS provider discovery and deterministic pricing
in Brisbane. **This repository currently contains tooling, register ingestion,
catalogue price limits, Maps-to-register entity resolution and polite website page
collection only, not a working provider navigator.** Python 3.12, FastAPI,
Pydantic v2, SQLAlchemy 2 and uv; the front end (ticket 09) is React/TypeScript in `web/`.

## Development

```sh
uv sync --locked
uv run pre-commit install
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run lint-imports && uv run pytest
uv run python scripts/check_no_stubs.py
uv run python scripts/check_private_data.py --tracked
```

Requires uv ≥ 0.13.7, the version CI pins; older uv cannot read the lockfile.
`uv.lock` is always resolved against PyPI by the CI `quality` job, which uploads
it as the `uv-lock` artifact and fails when the committed file differs. If your
machine resolves through a package mirror, do not commit the lock it writes:
run `git update-index --skip-worktree uv.lock` in your checkout, and after a
dependency change download the artifact from the failing run and commit that
(`git update-index --no-skip-worktree uv.lock` first).

Run `uv run pytest -m eval` once model-backed evaluation tests are added. CI gives
a visible notice when the suite or a usable model/key is unavailable. No LLM key
is required for tickets 00–04. Personal model configuration belongs in
`~/.config/ndis/.env` (see `.env.example`); environment-file loading is not built yet.
Never use workplace credentials.

## Register ingestion

```sh
uv run python scripts/fetch_public_data.py --only register   # -> data/public/register.csv
uv run python scripts/load_register.py                        # -> data/ndis.db, logs counts
uv run uvicorn ndis.api.app:app                               # GET /providers?state=&postcode=&group=&status=&brisbane_area=&limit=&offset=
```

The fetch script rejects (and deletes) a download that is not over 30 MB with more
than 25,000 distinct ABNs, which catches the truncated file the Commission's server
returns to non-browser user agents. The loader reads the CSV row by row and holds the
parsed providers/outlets in memory before one atomic write (~85 MB peak for the full
register); it stores one provider per ABN and one outlet per row nationally, and flags
`brisbane_area` when a head office or outlet postcode is in 4000–4207 or 4300–4305.
`NDIS_DB_PATH` overrides the database path.

## Support catalogue and price limits

```sh
uv run python scripts/fetch_public_data.py --only catalogue  # -> data/public/support_catalogue.xlsx
uv run python scripts/load_catalogue.py                       # -> data/ndis.db (support_items), logs counts
uv run uvicorn ndis.api.app:app                               # GET /price-limits?item=&state=&remoteness=
```

**Source.** "NDIS Support Catalogue 2026-27" (XLSX), linked from the NDIA page
<https://www.ndis.gov.au/providers/pricing-and-payments/pricing/what-support-catalogue>
(reached from the Pricing Arrangements page) as
<https://www.ndis.gov.au/media/8038/download?attachment>. The server names the file
`NDIS-Support-Catalogue-2026-27 v1_1.xlsx` (128,678 bytes); the workbook's core
properties carry no title or version, only a last-modified stamp of 2026-07-02T04:45:32Z.
Every row has a start date of 1–3 July 2026. The ndis.gov.au server answers only
browser-like user agents, so the fetch script sends one; it never relaxes TLS or proxy
settings. The XLSX is downloaded under the ignored `data/public/` and never committed. No
LLM is involved anywhere in this path.

**Verification.** The download is written to `.part` and kept only if it is over 100 KB and
contains more than 900 distinct support item numbers whose start date falls in 2026-27, so
an HTML error page, a truncated workbook or an older catalogue is deleted instead of kept.
The loader additionally refuses to write when fewer than 95% of rows parse.

**Sheets and columns.** Both sheets, `Current Support Items` (976 rows) and
`Legacy Support Items` (41 rows, items that end during 2026-27), share one header. The
columns used are:

| Column header | Field |
| --- | --- |
| `Support Item Number` | `item_number` (primary key) |
| `Support Item Name` | `name` |
| `Registration Group Number` | `registration_group_number` (text, keeps the leading zero) |
| `Registration Group Name` | `registration_group_name` |
| `Unit` | `unit` (`H`, `E`, `D`, `WK`, `MON`, `YR`) |
| `Quote` | `quotable` (`Yes`/`No`) |
| `Start date` | `start_date` (`YYYYMMDD`; must fall in 2026-27) |
| `End Date` | `end_date` (`99991231` → `null`, open-ended) |
| `National` | `limits.national` |
| `Remote` | `limits.remote` |
| `Very Remote` | `limits.very_remote` |

Support category, claim-type flags (`Non-Face-to-Face Support Provision`, `Provider
Travel`, …) and `Type` are not loaded. The sheet is read in order and the first row for an
item number wins, so a current item shadows a legacy duplicate (none exist in v1.1).

**Prices.** The 2026-27 catalogue publishes one `National` limit plus `Remote` and
`Very Remote` tiers; unlike earlier catalogues it has no per-state columns, so every
state shares the national figure. `price_limit(item, state, remoteness)` in `domain/`
still validates the state and returns the tier's `Decimal`, or `None` for quotable and
unpriced items (SIL weekly items, for example). Excel stores the prices as doubles
(`271.58999999999997` for 271.59); the parser takes Python's shortest round-trip text of
the cell and rejects anything that is not whole cents, so no rounding ever happens.
Limits are stored in SQLite as decimal text (`"82.57"`) and returned by the API as
strings. `GET /price-limits?item=01_002_0107_1_1&state=QLD` returns the national limit
with all three tiers; `remoteness=remote|very_remote` selects a tier; an unknown item is
404, an invalid state or remoteness is 422. Item numbers are matched exactly and
case-sensitively after trimming whitespace, because the catalogue contains the mixed-case
item number `Bereavement` alongside the usual `01_002_0107_1_1` form.

## Pricing rules RAG

```sh
uv run python scripts/fetch_public_data.py --only pricing_pdf  # -> data/public/pricing_arrangements.pdf
uv run python scripts/build_rule_index.py                      # -> data/rule_index.db, embeds with cache
uv run python scripts/rule_retrieval_report.py --answers       # -> reports/retrieval.md
uv run uvicorn ndis.api.app:app                                # POST /ask-rule {"question": "..."}
```

**Source.** "NDIS Pricing Arrangements and Price Limits 2025-26 v1.1" (PDF, 104 pages),
linked from
<https://www.ndis.gov.au/providers/pricing-and-payments/pricing/pricing-arrangements>
as <https://www.ndis.gov.au/media/8096/download?attachment>. The 2026-27 "pricing
schedules" on that page are price tables only; the written pricing rules (cancellations,
travel, GST, non-face-to-face claiming, …) live in this document, so this is the one the
index is built from. The media id changes with each new version; update
`PRICING_PDF_URL` in `scripts/fetch_public_data.py` and the README when a later release
is published. The download is kept only if it is a readable PDF of at least 50 pages over
500 KB; the site answers only browser-like user agents (and, on some networks, rejects
curl outright while a plain `urllib` request with the same header passes).

**Index.** `scripts/build_rule_index.py` chunks every page with two strategies — fixed
600-estimated-token windows and heading-aware chunks cut at the document's
table-of-contents section boundaries — and embeds each chunk once through the bounded
adapter, keyed by content hash in `data/private/embedding_cache.db`, so a rerun or a
resumed run never re-embeds a chunk. Chunks and vectors live in `data/rule_index.db`
(sqlite-vec, one vec0 table per strategy). The whole corpus is ~460 chunks and builds in
a few paced minutes on the free tier.

**Asking.** `POST /ask-rule` embeds the question, retrieves the top chunks of the
configured strategy (`RULE_CHUNK_STRATEGY`, default `heading`), refuses with
`refused: true` and a reason when the top similarity is under the threshold
(`RULE_SIMILARITY_THRESHOLD`, default 0.65 — see `reports/retrieval.md` for the measured
spread), or asks the judge model, which must claim support and cite pages. Every citation
is checked in `domain` to be a verbatim span of a retrieved chunk from the cited page; an
answer with no surviving citation is refused, never returned. The API answers only from
the built index (503 until `build_rule_index.py` has run); it never quotes a span the
document does not contain and never gives funding advice.

## Entity resolution (Maps places → register)

```sh
uv run python scripts/resolve_entities.py   # reads data/private/maps_brisbane.db, writes provider_links,
                                            # evals/er_sample.yaml and reports/entity_resolution.md
```

The script stops with an error when the private Maps export is missing. Each place is
linked by website domain first, then by normalised name, then by a `rapidfuzz` ratio over
the Brisbane-area subset with threshold 92. When a shared franchise domain or a common name
maps to several ABNs, only an exact name agreement or the place's postcode may break the
tie; otherwise the place is `ambiguous` with the candidates listed, and a lone name hit far
from Brisbane is `ambiguous` too. `gov.au` websites and Google categories on an explicit
non-provider deny list get `not_a_provider` unless the register knows them. Results go
to the `provider_links` table in `data/ndis.db` with `match_status` ∈ {registered,
unregistered, revoked_or_banned, ambiguous, not_a_provider}, a strategy and a confidence.
Fuzzy matches are never treated as registration evidence. The 50-row sample is stratified
by (status, strategy) and holds only Maps titles and register names/ABNs; the owner fills
`verdict:` and `uv run pytest -m eval --no-cov` reports precision over the filled labels
(and skips visibly when none are filled). See [the report](reports/entity_resolution.md).

## Website evidence (polite page collection)

```sh
uv run python scripts/collect_pages.py [--refresh] [--limit N] [--dry-run]
# seeds data/private/pages/ + page_meta from the Maps crawl, then fetches the gap live
```

For every `provider_links` row that is not `not_a_provider` (keyed by ABN when linked, by
place id otherwise) the script picks one website, the register's `Website` column first and
the Maps URL second, after normalising both (scheme added, host lower-cased, fragment and
`utm_*`/`gclid` parameters dropped, mangled values such as `https://188 George Street`
rejected). It first seeds the cache from the `ok` rows of the private Maps crawl's `pages`
table (blank text is stored as `empty`), skipping any `(provider_key, url)` the database
already holds so a crawl row never overwrites a later live result, then fetches live only
for providers with no `ok` page stored, or for all of them with `--refresh`. `--dry-run`
writes nothing: it counts the seeding and lists the fetches. Cleaned text is written to
`data/private/pages/<sha256(url)[:16]>.txt` (ignored by Git); the `page_meta` table in
`data/ndis.db` holds only `url`, `provider_key`, `fetched_at`, `status`, `text_hash`,
`source` (`maps_crawl` or `live`), `text_path` and `title`.

The fetcher sends `User-Agent: ndis-provider-navigator/0.1 (+repo URL)`, reads `robots.txt`
once per host (a 4xx means no rules; a 5xx, timeout or dropped connection means the rules are
unknown and the site is recorded `robots_unavailable`, not fetched), keeps requests to one
host at least 1 s apart (or the site's `Crawl-delay` if longer; over 30 s the site is
recorded `crawl_delay_too_long` and left alone), times out after 10 s, abandons bodies over
2 MB as `too_large`, records non-HTML answers as `empty` without reading them, and retries
exactly once on a timeout, failed or dropped connection or 5xx, never on 4xx or a robots
decision. Redirects are followed by hand for up to 5 hops, each hop through the robots check
and pacing of its own host. `trafilatura` strips navigation, footers and comments. Statuses:
`ok`, `empty`, `robots_disallowed`, `robots_unavailable`, `crawl_delay_too_long`, `timeout`,
`http_error`, `connection_error`, `too_large`. No LLM is involved; nothing from the pages is
published.

## Architecture

- `src/ndis/domain/`: pure entities and rules, no frameworks.
- `src/ndis/application/`: ports and use cases, depends inward on domain.
- `src/ndis/adapters/`: database, HTTP, model and tracing implementations.
- `src/ndis/api/`: FastAPI composition; `web/`: React/TypeScript front end (ticket 09; replaces the Streamlit page and the `src/ndis/ui/` package).

Import-linter enforces inward dependencies and forbids framework dependencies in
the inner layers. Standard-library imports are allowed. Strict typing covers
domain and application; tests enforce at least 80% coverage of those layers.
Empty layers in ticket 00 contain no business behavior to measure.

## Data sources and privacy

The NDIS Commission public provider register is the provider source of record.
Services will be supported by quotes from providers' websites, not inferred from
registration groups. Pricing will come from the official support catalogue and
Pricing Arrangements and Price Limits PDF. Download scripts, not source data,
will be committed in subsequent tickets.

Maps exports are local bootstrap material only. Keep them and fetched page text
under ignored `data/private/`; never stage them. The pre-commit hook blocks staged
private data, and CI checks all tracked private paths. Database files, public
downloads, local environment files and scraper directories are also ignored.
The existing parent-directory exports must stay outside this repository.

## Delivery harness

One issue, worktree, branch and squash-merged PR per ticket. CI must be green and
an independent Reviewer agent must post `APPROVE` as a PR comment before merge.
`main` is protected with the required `quality` status check; no GitHub approving
review is required because the project has a single account.
See [resume state](docs/harness/STATE.md) for progress and next actions.
Learning notes are added after each merge under `docs/learning/`.

## Not built yet

Pricing Arrangements PDF rule retrieval; evidence-backed extraction; intent parsing and
refusal guards; measured model evaluations; tracing; the web front end; geographic ranking;
and the fresh-clone demo. No service or rule answer should be considered available at this
stage; the provider endpoint lists register facts only and the price-limit endpoint returns
catalogue limits only.
