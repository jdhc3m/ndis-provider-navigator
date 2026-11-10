# Foreman resume state

Updated: 2026-11-10 (Australia/Brisbane), after ticket 10 merged. Tickets 05–08 ran on the second machine (the one with Gemini access; see "Machine handoff"); ticket 09 ran on the corporate Mac.

A new Foreman reads this file, `gh issue list --label ticket --state open`, and
`gh pr list`, finishes any open PR first, then continues with the lowest-numbered
open issue whose dependencies are closed. The full brief lives outside the repo at
`~/NDIS/HARNESS_PROMPT.md`; read it before resuming.

## Environment facts the next Foreman needs

- Work on the owner's Mac, not in an orb. The Mac sits behind a corporate proxy:
  `uv sync` only resolves while the VPN is connected. Hosted LLM endpoints are
  blocked on it, so tickets 05–08 and the live demo need another machine for the
  LLM-backed parts. Never bypass the proxy or disable TLS.
- Use `GH_HOST=github.com` for every `gh` call; the Mac has a second, expired
  `gh` login that is otherwise picked by default. Account is `jdhc3m` (free plan).
- The repo is **public**. `main` protection uses required status checks only
  (`quality` job); no approving review is required because there is no second
  account. The Reviewer subagent's `APPROVE` is posted as a PR comment and is the
  approval record.
- Private inputs (the Maps SQLite export, the Maps CSV exports and the scraper
  kit) live in the parent directory `~/NDIS/`, outside the repo.
  `git log --all -- data/private` must stay empty.
- Canonical checkout: `~/NDIS/ndis-provider-navigator` (`main`).
  Ticket worktrees: `~/NDIS/ndis-provider-navigator-ticket-NN`.
- **Lockfile.** The Mac cannot reach PyPI and CI cannot reach the mirror, and
  the mirror ships a patched `certifi` that is not on PyPI, so `uv.lock` can only
  be produced by CI. The `quality` job runs `uv lock`, uploads `uv-lock`, and
  fails if the committed lock differs. Per worktree run
  `git update-index --skip-worktree uv.lock` so local mirror locks are never
  staged. After a builder changes dependencies: push, let CI fail on the lock
  step, then `gh run download <run-id> -n uv-lock -D /tmp/uvlock`,
  `git update-index --no-skip-worktree uv.lock`, copy it in, commit
  `chore: lock dependencies against PyPI`, re-set skip-worktree, push.
- Harness loop that worked for tickets 01 and 02: one fresh Builder subagent
  with the issue text, AC, AGENTS.md, gate command and the lock rule (never
  commit `uv.lock`; new deps must also go into the import-linter denylist); one
  fresh Reviewer subagent given the worktree, issue and AC — not the builder's
  summary — told to verify by running probes in a `/tmp` copy; relay must-fix
  items verbatim back to the same Builder; re-review; then push, PR with
  `--body-file`, Reviewer record as a PR comment, lock commit from the
  `uv-lock` artifact, squash-merge, docs PR for the note. Each ticket took two
  review rounds; the Reviewer found a real defect both times.
- Write PR/comment bodies to files under `.amp/in/` and pass `--body-file`;
  heredocs with apostrophes inside `$(...)` break.
- `pytest -m eval` runs with `--no-cov` in CI: the coverage gate applies to the
  unit/integration run, not the eval subset. Ticket 08 owns the eval gate design.

## Decision 2026-11-09: ticket 09 is a React/TypeScript front end, not Streamlit

The owner chose a TypeScript web page. Issue #10 and the brief (§2, §5 ticket 09,
§6 AGENTS text, ticket 11 prerequisites) were rewritten; AGENTS.md and README
updated. Facts the Builder of ticket 09 needs:

- Stack: React 19 + TypeScript strict + Vite in `web/`, Tailwind CSS v4, pnpm,
  Node 22. Fonts self-hosted (`@fontsource/*`); no CDN. Typed client generated
  from `/openapi.json` with `openapi-typescript`; CI fails on schema drift.
- Design comes from the `ui-ux-pro-max` skill installed globally on the Mac at
  `~/.agents/skills/ui-ux-pro-max/` (Amp loads it; subagents can call `skill`
  with name `ui-ux-pro-max`). Its generator for this product proposed the
  "Accessible & Ethical" style, blue primary, Atkinson Hyperlegible, no
  gradients; persist the output as `web/design-system/MASTER.md`.
- pnpm works through the corporate npm mirror (`~/.npmrc`) and its lockfile
  stores integrity hashes only, so `pnpm-lock.yaml` is committed normally; CI
  installs from npmjs with `--frozen-lockfile`. Do not apply the `uv.lock`
  skip-worktree dance to it. Playwright's browser download may be blocked by
  the proxy; screenshots may be taken with the owner's browser instead.
- `streamlit` is still a declared dependency in `pyproject.toml` and `ndis.ui`
  is still in the import-linter contract; ticket 09 removes both (expect the
  CI lock step to fail once and commit the `uv-lock` artifact as usual).

## Done

- Ticket 00 — repo, tooling, CI, protections (issue #1, PR #16). Note:
  `docs/learning/00-repo-tooling.md`.
- Ticket 01 — register ingestion (issue #2, PR #19). Note:
  `docs/learning/01-register-ingestion.md`. The verified full register copy is
  kept outside the repo; `scripts/fetch_public_data.py` refuses truncated
  downloads (size > 30 MB and > 25,000 distinct ABNs required).
- Ticket 02 — support catalogue and price limits (issue #3, PR #21). Note:
  `docs/learning/02-support-catalogue.md`. The 2026-27 catalogue (v1.1) has no
  per-state price columns, only National/Remote/Very Remote; `state=` is
  validated and returns the national tier. Documented in README; do not
  "fix" this by inventing state prices.

- Ticket 03 — private data bootstrap and entity resolution (issue #4, PR #23). Note:
  `docs/learning/03-entity-resolution.md`. `scripts/resolve_entities.py` needs
  `data/private/maps_brisbane.db` (copy of `~/NDIS/ndis.db`) and a loaded
  `data/ndis.db`; it writes `provider_links` (with Maps lat/lon for ticket 10),
  `evals/er_sample.yaml` (50 rows, verdicts blank — the owner fills them) and
  `reports/entity_resolution.md`. Measured 223 registered / 120 unregistered /
  6 revoked / 16 ambiguous / 50 not_a_provider. Known follow-ups are on issue #4.
  If a local `data/ndis.db` predates this ticket, `provider_links` must be
  dropped once before re-running (the table gained a `candidates` column).

- Ticket 04 — website fetcher (issue #5, PR #26). Note:
  `docs/learning/04-website-fetcher.md`. `scripts/collect_pages.py` seeds from
  the Maps `pages` table once, then fetches live only for providers with no
  cached `ok` page (`--dry-run` writes nothing). Locally 288/334 providers are
  covered from the crawl; 46 were fetched live through the corporate proxy and
  mostly failed (403 proxy block pages, TLS failures on robots.txt), so a run
  from an unfiltered network will retry exactly those 46 automatically. Page
  text lives only under `data/private/pages/`; `page_meta` holds url,
  fetched_at, status, source, text_hash, text_path, title.

- Ticket 05 — service extraction (issue #6, PR #30). Note:
  `docs/learning/05-service-extraction.md`. Headline on the 10 agent-labelled
  sites: LLM macro P 73% / R 88% vs regex baseline 56% / 93% (baseline =
  `provider_services`, 3,746 rows over 326 places). Adapter is
  OpenAI-compatible over httpx (no new dependency); extraction cache lives
  under `data/private/extraction/` per provider (resumable), projected into
  public-safe `service_claims` / `provider_attributes` tables. Two builder
  rounds were needed: the reviewer caught an inflated metrics table (row
  unpacking read truth from the prediction column) and the eval metric living
  only in a script, not under `pytest -m eval`. Caveat: the labelled run
  finished on `gemini-3.5-flash-lite` via `LLM_MODEL` override because
  `gemini-3.5-flash` hit its daily free-tier quota (429, ~21 h reset) — one
  model across all predictions, cache uniform. The full ~300-provider run is
  still pending and should be paced/chunked after the quota resets.

- Ticket 06 — pricing rules RAG (issue #7, PR #32). Note:
  `docs/learning/06-pricing-rules.md`. hit@3 100% on both chunking strategies
  (10 golden questions, pages verified against the PDF). Corpus: PAPL
  2025-26 v1.1, 104 pages, 175 fixed / 284 heading-aware chunks, 3072-dim
  embeddings, sqlite-vec index `data/rule_index.db` (26 MB); embedding cache
  `data/private/embedding_cache.db` keyed sha256(model+text). Threshold 0.65
  (`RULE_SIMILARITY_THRESHOLD`); off-topic band max 0.620. New deps:
  `sqlite-vec`, `pymupdf` (pinned <2; no x86_64 macOS wheel for 2.x). The
  written rules live in the 2025-26 PAPL — the 2026-27 page offers schedules
  (tables) only. Chat quota 429s recur daily (~20 h reset); retrieval and the
  eval are cache-only and unaffected, sample answer generation may need the
  `LLM_MODEL=gemini-3.5-flash-lite` override. The eval gate command is
  `uv run pytest -m eval --no-cov` everywhere now (AGENTS.md, README).

- Ticket 07 — intent parsing and the combined answer (issue #8, PR #34).
  Note: `docs/learning/07-combined-answer.md`. `POST /ask`: intent parse is
  the only new LLM call site; everything else deterministic. Output guard
  `domain/answer.py.check_answer` (fabricated providers, quote-less service
  claims, caveat symmetry — byte-exact `CAVEAT_SERVICES` constant). Golden:
  12 exact-match filter cases + 5 refusal/adversarial cases appended to
  `evals/golden.yaml` (ticket 06 rule questions untouched); intent parses
  replayed from `evals/intent_cache.yaml` (17 captures, no secrets).
  Filter accuracy 12/12, refusals 5/5. Zero new dependencies. Known:
  `/providers` SQL latency ~3–9 s/query reused unchanged (pre-existing).

- Ticket 08 — evals as a CI gate, tracing, cost (issue #9, PR #37). Note:
  `docs/learning/08-evaluation-gates.md`. Thresholds owned in
  `evals/thresholds.yaml` and enforced by the eval tests (probed: lowering a
  threshold turns its eval red). Measured: filter 1.0, refusal recall 1.0,
  hit@3 1.0, extraction P 0.840 served / 0.732 raw (floor 0.75 at serve
  time, disclosed; recall 0.905 unchanged). Judge agreement 7/10 = 0.70 (10
  agent-labelled; 10 await the owner). Langfuse tracing wraps every LLM call
  site, lazy no-op without keys; `langfuse` added (sanctioned). CI gained a
  "Model evaluation gate" job (replay suites + prompt-contract test; data
  suites skip visibly). Red-CI demo: run 42630973402, draft PR #36 closed
  unmerged, scratch branch deleted. Cost in `reports/cost.md`. NOTE: the
  pending ~300-provider extraction run (≈ $1.34 est.) is still to do — pace
  it after the daily quota resets.

- Ticket 09 — web front end in React/TypeScript (issue #10, PR #39, squash
  `69da8b5`). Note: `docs/learning/09-web-frontend.md`. `web/` (React 19,
  TS strict, Vite, Tailwind v4, pnpm) single page over `POST /ask` + new
  `GET /meta`; `AskOut.steps` recorded inside `Ask.__call__` (injectable
  clock, cost from prices injected by the composition root); FastAPI mounts
  `web/dist` at `/` when present (`WEB_DIST_DIR`), CORS from `CORS_ORIGINS`
  (default localhost:5173 + 127.0.0.1:5173); `src/ndis/ui/` and `streamlit`
  removed, layers now `api : adapters -> application -> domain`;
  `scripts/export_openapi.py` + `web/src/api/schema.d.ts` via
  openapi-typescript, diffed in CI; CI `web` job added and made a required
  check (contexts `["quality","web"]`). Measured: 667 passed / 6 skipped,
  coverage ≈95%; 16 Vitest tests; bundle 247 kB (76 kB gzip); CI `web` 50 s.
  Fixture-only screenshots in `docs/screenshots/`. Review: 0 must-fix.
  Follow-ups (not ticketed): `evals/test_extraction.py` creates an empty
  `data/ndis.db` as a side effect (since ticket 08) which un-skips the golden
  suite on a later data-less run; `rule_answer` step carries no tokens/cost
  (table says "Total (priced steps)"); `web/pnpm-workspace.yaml` has
  point-in-time `minimumReleaseAgeExclude` pins for `@typescript-eslint/*`;
  textarea border contrast 1.23:1 vs WCAG 1.4.11. NOT verified here: live
  `POST /ask` through the page (Gemini blocked, no `data/rule_index.db`) —
  check once on the second machine with `pnpm --dir web build` then
  `uv run uvicorn ndis.api.app:app --port 8000`.
  Foreman lessons: Task subagents failed with "Model is not supported:
  anthropic/claude-fable-5-2" although the main thread runs it — fixed by
  pinning `mode_model_overrides` ultra→`anthropic/claude-fable-5-1` (effort
  high); do NOT symlink `data/` into a worktree (`tests/test_collect_pages.py`
  runs `git check-ignore`, which fails across the symlink) — point
  `NDIS_DB_PATH` at the canonical checkout's `data/ndis.db` instead; after a
  dependency change on the Mac do the lock dance (push → `quality` fails →
  `gh run download <id> -n uv-lock` → commit `chore: lock dependencies
  against PyPI`).

- Ticket 10 — geo ranking (issue #11, PR #41, squash `2df8748`). Note:
  `docs/learning/10-geo-ranking.md`. `domain/geo.py` (haversine R =
  6371.0088 km, `Distance{km, source: exact|postcode_centroid}`,
  `rank_by_distance` over the whole matched set before pagination, inclusive
  `max_km` on the shown 1-dp value); `postcode_centroids` table (3,173
  postcodes; mean of located rows; `0,0` rows rejected; 9999 "North Pole"
  excluded by the explicit `NON_GEOGRAPHIC_POSTCODES` list — owner decision;
  PO-box postcodes kept) from Matthew Proctor's public CSV via
  `scripts/fetch_public_data.py --only postcodes` + `scripts/load_postcodes.py`
  (loaded into the canonical Mac `ndis.db`; the second machine must run both
  scripts once). `GET /providers?near=&max_km=` + `distance_origin`; `/ask`
  uses `intent.postcode`, falls back without distances on unknown postcode and
  says so in the `provider_filter` step. Cards show km + source label.
  Measured: 736 passed / 6 skipped, coverage 95.0%; `near` ranking ≈8 ms over
  29,574 providers, 0.08 s end to end. Review: APPROVE, 0 must-fix.
  Follow-ups (not ticketed): providers matched by a Brisbane outlet but with
  an interstate head office get the head-office centroid (nearest-outlet
  postcode would be better); `exact_coordinates()` loads all 223 registered
  links per request (fine today). Reviewer-brief template at
  `~/NDIS/.amp/in/ticket-10/reviewer-brief.md`.

## In progress

- None.

## Blocked

- Nothing on this machine: the handoff items (valid `LLM_API_KEY` + env vars,
  the brief, the private-data copy) are all resolved — see "Machine handoff".
  On the corporate Mac, tickets 05–08, the eval gate and the live demo stay
  blocked: Zscaler returns a 403 block page for
  `https://generativelanguage.googleapis.com/` regardless of the key.

## Machine handoff (2026-11-09)

The owner has set `LLM_API_KEY` in `~/.config/ndis/.env` on a second machine
that can reach the Gemini API. Ticket 05 and everything after it run there.
Before the first ticket on that machine, the Foreman must check and, where
missing, ask the owner for:

- `~/.config/ndis/.env` with `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` and
  `EMBED_MODEL` as in `.env.example`. Prove reachability once with a real
  request (list models through `LLM_BASE_URL`) before briefing the Builder.
- `~/NDIS/HARNESS_PROMPT.md` (the brief lives outside the repo; copy it from
  the Mac) and the Maps export `~/NDIS/ndis.db` (private, never committed).
- `gh auth login` as `jdhc3m`. The `GH_HOST=github.com` rule and the
  mirror/VPN facts above are Mac-specific; check whether `uv sync` reaches
  PyPI directly on the new machine. The lockfile rule still applies unless
  `uv lock` there matches CI's PyPI resolution — test it once with
  `uv lock --check` before relying on it.
- The repo clone at `~/NDIS/ndis-provider-navigator`, `uv sync`, then either
  copy the ignored `data/` directory from the Mac (it is complete there as of
  this update: register, catalogue, `maps_brisbane.db`, `provider_links`,
  `page_meta`, 1916 seeded + 50 live pages) or rebuild it:
  `scripts/fetch_public_data.py`, `scripts/load_register.py`,
  `scripts/load_catalogue.py`, copy `~/NDIS/ndis.db` to
  `data/private/maps_brisbane.db`, `scripts/resolve_entities.py`,
  `scripts/collect_pages.py`.
- For ticket 09 later: the `ui-ux-pro-max` skill is installed globally on the
  Mac at `~/.agents/skills/ui-ux-pro-max/`; install it on the new machine
  before briefing that Builder.

Free-tier Gemini limits are per minute and per day; ticket 05's Builder should
pace or chunk the ~300-provider extraction run and cache results so a rerun
does not repeat calls.

### Re-verified 2026-11-09 on the second machine

Green:

- The machine reaches `https://generativelanguage.googleapis.com/` directly
  (real API error payloads, no Zscaler 403 page) — it is the right machine for
  tickets 05–08.
- `gh` is logged in as `jdhc3m` and `github.com` is the only configured host,
  so the `GH_HOST=github.com` workaround is not needed here.
- Repo cloned, tree clean, `main` up to date at `~/ndis-provider-navigator`
  (not the `~/NDIS/ndis-provider-navigator` path the brief uses; accepted for
  now, but `HARNESS_PROMPT.md` and the Maps export should still land in
  `~/NDIS/`).
- `uv sync` and `uv lock --check` pass against PyPI directly (exit 0), so the
  skip-worktree lock dance is not needed on this machine; re-run
  `uv lock --check` after any dependency change before trusting it again.
- Board: ticket 05 = issue #6 is next, no open PRs;
  `git log --all -- data/private` is empty.

Still missing before the Builder can be briefed — **all resolved later the
same day**: the API key was replaced with the bare `AQ.…` key (proven with a
real list-models request: HTTP 200, 61 models, both configured models
present), the three missing env vars were added, and `HARNESS_PROMPT.md`, the
Maps export and the complete `data/` directory arrived via the private repo
`jdhc3m/ndis-handoff` (bundle `ndis-handoff-2026-11-09.zip`: `HARNESS_PROMPT.md`,
`ndis.db`, `data/` copied to the canonical checkout, `ui-ux-pro-max` skill
installed at `~/.agents/skills/`). Data verified: `providers` 29,574,
`outlets` 64,734, `support_items` 1,017, `provider_links` 415, `page_meta`
1,951 (1,901 crawl + 50 live; 1,824 distinct text files, none missing —
hash-deduped, 34 rows share files). Note: STATE previously said "1916
seeded"; the bundle has 1,901 crawl rows — count drift, everything
referenced exists. The Maps regex taxonomy lives in the DB `services` table
(36 slugs), so the Mac-only `~/NDIS/db/services.py` file was not needed.

## Next

- Ticket 11 — README, demo script, limitations (issue #12). Needs the second
  machine for the live `/ask` walkthrough (tickets 09 and 10 each have a
  "not verified live" item: `/ask` through the page, and `/ask` distances);
  on that machine also run `fetch_public_data.py --only postcodes` and
  `load_postcodes.py` once so `/ask` has centroids.
- Ticket 12 — stretch (issue #13), only if time remains.
- This Foreman thread has processed tickets 09 and 10 (limit 3 per thread);
  hand off after ticket 11 at the latest.
