# Foreman resume state

Updated: 2026-11-23 (Australia/Brisbane), after ticket 16 merged (PR #68, squash
`0818cfb`) and the leaner-process decision below. Tickets 00–16 are on `main` with
green CI. The board now carries tickets 17–24 (issues #56–#63, created 2026-11-22)
— a LangGraph/chat/research phase; see "Decision 2026-11-22" below. Tickets 14
and 15 each consumed a full day; the owner approved process cuts (see "Decision
2026-11-23: leaner harness process") that bind the loop from ticket 17 on —
ticket 16 merged before the decision landed and ran on the old process.

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
  (Superseded for tickets 16+ by the leaner-process decision below.)
- Write PR/comment bodies to files under `.amp/in/` and pass `--body-file`;
  heredocs with apostrophes inside `$(...)` break.
- `pytest -m eval` runs with `--no-cov` in CI: the coverage gate applies to the
  unit/integration run, not the eval subset. Ticket 08 owns the eval gate design.

## Decision 2026-11-23: leaner harness process (applies from ticket 16)

Tickets 13–15 each took a full day. Measured where the time went: a third feature
work, a third tests/eval, a third process the last three tickets did not need
(the Reviewer returned 0 must-fix in 13–15; a second docs PR per ticket; the
~12:45 of register-backed evals re-ran on every gate pass; full 35-case capture
re-spend after each prompt tweak; demo re-run even when the demo output did not
change). The owner approved the following cuts; they bind the Foreman loop from
ticket 16 on:

- **One PR per ticket.** Feature, learning note and STATE update go into the
  same branch and squash-merge together — no separate docs PR. Commit order in
  the branch: `feat:` (or `fix:`) first, then `docs:` for note + STATE.
- **One review round by default.** A second round happens only if round one
  returned must-fix items; nits become STATE follow-ups, not re-review work.
- **Reviewer depth by ticket type.** Tickets introducing a new class of
  behaviour — next: 16 (checkpointer), 17 (chat UI), 18 (interrupt) — get the
  full Reviewer (fresh `/tmp` copy, probes). A mechanical or infra-only ticket
  with no user-visible change gets a scoped review: read the diff, run the
  gates, no `/tmp` copy.
- **Web ceremony only when `web/` changed.** No web change → no screenshots, no
  `pnpm check`, no demo re-run. Re-run `docs/demo.md` live only when the demo's
  shown output changes (ticket 14 rewrote the price answer; ticket 15 did not).
- **Eval split (implemented).** `test_ask_golden` and `test_ask_graph_parity`
  carry a `slow` marker and the default pytest adds `-m 'not slow'`, so
  `uv run pytest` (the AGENTS gate) is units + fast evals; run the full gate
  `uv run pytest -m eval --no-cov` once before push — CI's eval job selects
  everything again (`-m eval` overrides the default). Fast subset alone:
  `uv run pytest -m 'eval and not slow' --no-cov`.
- **Partial captures (implemented).** After a prompt tweak or reworded golden
  case, capture only what changed:
  `scripts/parse_intent_cache.py --only SUBSTRING` (repeatable) keeps the other
  payloads from the committed cache. Full re-capture only when the prompt or the
  golden set changes wholesale.
- **Coverage scope stays domain+application.** Graph node closures and the
  composition root are outside the measured denominator on purpose; do not add
  them to `source`, and do not write mirror tests for thin wiring — the shape
  tests (fan-in once, refusal edge, thread safety) and the parity eval are their
  coverage.

## Decision 2026-11-22: LangGraph and chat (tickets 15–24)

The owner created and approved tickets 15–24 (issues #54–#63): the brief's
"agent frameworks: none for the core" rule is superseded for these tickets —
LangGraph is used where a ticket says so. `POST /ask`, the `Ask` use case and
the eval gate stay as they are; the graph is added beside them. Model calls
keep going through the `LlmClient` port — no LangChain chat-model package — so
the vendor rule in AGENTS.md still holds. The tickets:

- 15 — LangGraph foundations: one question through a graph (#54)
- 16 — Conversation memory: threads, checkpoints and follow-up questions (#55)
- 17 — Chat page in the web front end (#56)
- 18 — Clarifying questions: pause and resume with interrupt (#57)
- 19 — Live progress: stream graph steps to the chat (#58)
- 20 — Durable chat memory in Supabase Postgres (#59)
- 21 — Provider research graph: parallel tools, a loop and a findings cache (#60)
- 22 — Research inside the chat: routing, subgraph and the dossier card (#61)
- 23 — ABN Lookup as a research tool (#62)
- 24 — Model-driven research agent and the fixed-versus-agent eval (#63)

LangGraph 1.2.x (`langgraph>=1.2,<2`); confirm APIs against the locked
version's docs before relying on them. AGENTS.md gains the LangGraph paragraph
in ticket 15. Graphs live only in `src/ndis/adapters/graphs/`; checkpointed
state holds JSON-plain values and never page text.

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

- Ticket 11 — README, demo script, limitations (issue #12, PR #43, squash
  `495009b`). Note: `docs/learning/11-demo-documentation.md`. README rewritten
  (Mermaid diagram validated with mermaid 11; data sources + verified licences
  incl. PyMuPDF AGPL named; public/private bootstrap steps; measured results
  copied from `reports/`; Not-built-yet list with the six required items plus
  the ticket 09/10 follow-ups); `docs/demo.md` (five-minute script — every
  command actually run, shows intent chips, evidence quotes, deterministic
  price, page-26 rule citation, two refusals, eval gate, 429→502 recovery);
  `docs/recording-checklist.md` (8 shots, exactly 180 s). Builder verified a
  fresh clone in `/tmp` through the whole public path (register 42.5 MB ≈ 4
  min → 29,574 providers; catalogue 1,017; postcodes 3,173; rule index ≈ 5
  min / ~135k tokens; `pnpm install && build` — README now documents
  `corepack prepare pnpm@11.11.0 --activate` for pnpm-version mismatch) and
  the key-dependent steps (`/ask`, `/ask-rule`, refusal; free-tier 429→502
  reproduced and recovered via the STATE fallback model). Prerequisites done
  on this machine first: `fetch_public_data.py --only postcodes` +
  `load_postcodes.py` (3,173 centroids), LLM reachability proven (list-models
  HTTP 200). CLOSED both "not verified live" items: `/ask` driven through the
  built page in a real browser (ticket 09) and `/ask` distances render — 20
  of 36 providers from 4120, 9 `exact` / 11 `postcode_centroid` (ticket 10).
  Measured: 736 passed / 6 skipped, coverage 95%; eval 8 passed / 1 skipped
  (~97 s). Review: REQUEST_CHANGES round 1 (must-fix: recording-checklist
  shots sum to 180 s, not "~170 s"; nits: model-dependent chip annotation,
  uv pin wording) → fixed `1cb7118` → APPROVE.
  Follow-ups (not ticketed): `adapters/ask_evidence.py` never populates
  `ClaimedService.fetched_at` (API `services[].fetched_at` always null —
  cosmetic); the brief's showcase question ("registered OT for a 7-year-old
  near 4101 who does home visits, and what's the hourly price limit?")
  parses as `decision_request` and refuses under both models tried —
  demo.md answers it as two split questions; Langfuse keys are empty in
  `~/.config/ndis/.env` on this machine so tracing was verified only via the
  `/ask` steps table and `/meta.tracing.configured=false` — for a live
   Langfuse demo, ask the owner to add the keys.

- Ticket 12 — Snowflake Cortex Search comparison, stretch (issue #13, PR #45,
  squash `46179d7`). Note: `docs/learning/12-snowflake.md`; report:
  `reports/snowflake.md`. The 284 served heading chunks + their 3072-dim
  vectors were read out of the local `sqlite-vec` index and uploaded to a
  personal Snowflake trial (`wt38369.ap-southeast-2`, key-pair service user
  `NDIS_SERVICE`, no private data, no workplace credentials); a Cortex Search
  service was created over a `VECTOR(FLOAT, 3072)` column and queried with
  `SNOWFLAKE.CORTEX.SEARCH_PREVIEW` on the identical 10 golden questions +
  off-topic case, same cached query embeddings. Trial accounts cannot run the
  managed-embedding AI functions (`EMBED_TEXT_768 not available`), so
  embeddings were pinned on both sides — a controlled serving-engine
  comparison. Measured: hit@3 10/10 on both engines with identical top-3
  ordering; off-topic case refused locally (0.620 < 0.65) but Cortex Search
  has no refusal gate; latency median 283 ms Snowflake vs ~5 ms local;
  0.1994 XSMALL credits + 0.0003 cloud-services credits for the whole
  session (trial balance still $400/$400). Review: APPROVE, 0 must-fix
  (approval record: PR comment 6974929604). Follow-ups (not ticketed):
  hardcoded session constants in `scripts/snowflake_compare.py`'s report
  block; no-op `TRUNCATE` + non-atomic DROP→CREATE in
  `scripts/snowflake_load.py`; `snowflake_configured()` should also require
  `SNOWFLAKE_DATABASE`/`SNOWFLAKE_SCHEMA`; bare KeyError when the PDF and
  the built index drift; `.env.example` lacks the `SNOWFLAKE_*` keys;
  "180–725 ms observed" in the note could say "individual runs". The trial
  expires 28 days from 2026-11-10 — recreate user/warehouse/service if the
  comparison is ever re-run.

- 13b — Bugfix (issue #48, PR #49, squash `1a9998f`; no note, the PR body is
  the record). Found live on 2026-11-19: `/ask` rule questions 500'd with
  `sqlite3.ProgrammingError` because `SqliteVecRuleStore` held one connection
  (`check_same_thread=True`) cached behind an `@lru_cache` singleton —
  FastAPI's AnyIO threadpool creates it in one worker thread and any later
  request on another worker thread crashes. Latent since ticket 06; earlier
  live demos reused the same thread by luck. Fix: `check_same_thread=False`
  + one store-owned `RLock` around every statement (RLock for the
  re-entrancy of `search`→`count`/`dimension`); two regression tests that
  fail on the old code. Review: APPROVE, 0 must-fix. Latent-bug lesson for
  the next Foreman: TestClient integration tests run in one thread and
  cannot catch cross-thread defects — probe the real server (`uvicorn`) for
  anything involving shared adapters.

- Ticket 13 — Suburb names in provider questions (issue #50, PR #52, squash
  `0efa55c`). Note: `docs/learning/13-suburb-names.md`. Gazetteer from the
  same postcodes CSV as ticket 10: 17,984 Delivery-Area rows → 17,959
  `localities` persisted into the canonical `data/ndis.db` (additive;
  centroids untouched); intent gains `locality` (copied verbatim); pure
  resolver in `domain/locality.py` behind application ports — resolved /
  `locality_unknown` / `locality_ambiguous(candidates)`; unknown and
  ambiguous run no provider query while price/rule still answer; resolved
  reuses the postcode filter + distance origin unchanged and discloses the
  resolution; guards: model-invented `postcode` not in the question text is
  dropped (`postcode_not_in_question`), suburb contradicting the stated
  postcode is reported (`locality_postcode_mismatch`, truthful 422 message
  after the round-1 must-fix); `GET /providers?locality=&state=` with 422 +
  candidates; web resolution chip + ambiguous re-ask buttons + one suburb
  example chip; README updated. Measured: golden 7 locality cases (5
  resolve with re-verified register totals: 4101=44, 4152=25, 4006=23,
  4217=75, 4000=94; 1 ambiguous Southbank QLD/VIC; 1 unknown), filter
  12/12, refusals 6/6, locality 7/7; intent_cache regenerated (25 payloads,
  zero-tolerance parse net restored); 792 passed / 7 skipped locally;
  eval with register 9 passed / 3 skipped; 18 Vitest tests; screenshots
  `locality-*`/`ambiguous-*` at 1440/375 px. The intent captures were taken
  via the `LLM_MODEL` env fallback because the configured model's daily
  quota was exhausted — no model name committed, all captures verified
  through the real parser. Review: round 1 APPROVE + 1 must-fix (fixed),
  round 2 APPROVE 0 must-fix. Follow-ups (not ticketed): none recorded;
  out-of-scope stands (region names, typo correction, street addresses,
  suburb-coordinate origins, free-text suburb filtering).

- Ticket 14 — Service names instead of support item numbers (issue #51, PR
  #64, squash `098722a`). Note: `docs/learning/14-service-names.md`. Pure
  service search in `domain/service_search.py` (deterministic normalisation +
  test-pinned alias table for OT/physio/speech therapy/psychology/support
  coordinator/plan manager; ordered-contiguous token matching; ordering base
  before claim-type variants, current before legacy, then item number; cap 12
  shown), behind the repository port (`all_items()` added). `GET
  /support-items?q=&limit=` (total + page; no key, no model). Intent:
  `price_query.service` copied verbatim + optional `state`; `item` honoured
  only when present in the question text (`item_not_in_question`, never looked
  up); `price_query_no_state` removed — national tier is used and disclosed
  (the 2026-27 catalogue has no per-state prices, ticket 02). `POST /ask`
  price section lists matching items with limits + total, each marked from the
  catalogue; no match = `unknown_service`, nothing invented; output guard
  recomputes every shown price (`price_limit_mismatch`/`price_state_invalid`)
  in `domain/answer.py`. Web: `ServicePriceCard` (name, item number, unit,
  limit, "from support catalogue — not generated" chip), phrase chip with match
  count, unknown-service state, example chip now asks for occupational therapy;
  `schema.d.ts` regenerated. Eval: 10 `service_cases` in golden.yaml (8 with
  XLSX-verified expected item numbers incl. SA+providers, no-state, "OT"
  abbreviation; 1 unknown; 1 adversarial "state the code and price yourself");
  new floor `service_resolution_accuracy: 0.85`, the four existing floors
  byte-identical; `scripts/parse_intent_cache.py` extended to capture
  service_cases; all 35 captures regenerated under the new prompt (via the
  `LLM_MODEL` env fallback — configured model's daily quota exhausted; no
  model name committed). `docs/demo.md` re-run live as written (2026-11-23).
  Measured: service_resolution_accuracy 8/8 = 1.0; filter 12/12, refusals 6/6;
  865 passed / 14 skipped, coverage 95.36%; eval 11 passed / 3 skipped; 22
  Vitest tests. Review: APPROVE round 1, 0 must-fix (approval record: PR
  comment on #64); 5 non-blocking nits. Follow-ups (not ticketed): dead
  `MAX_SERVICE_PHRASE_CHARS` in `domain/service_search.py` (real cap is
  `MAX_SERVICE_CHARS` in `domain/intent.py`); singular/plural in
  `ServicePrices.output_summary` ("1 matching support items");
  `_contains_subsequence` actually requires a contiguous token run (rename);
  three older eval replays call `parse_intent` without `question=`;
  `docs/demo.md` eval-gate duration "~3–5 min" measured 5:48 on this machine.

- Ticket 15 — LangGraph foundations (issue #54, PR #65, squash `4f94d2a`).
  Note: `docs/learning/15-langgraph-foundations.md`; concept map:
  `docs/learning/langgraph-map.md`; diagram: `docs/graphs/ask_graph.md`
  (drift-tested). `langgraph>=1.2,<2` locked at 1.2.24 (additions only in
  `uv.lock`; CI lock step green first try — this machine's `uv lock` matches
  PyPI); `langgraph`+`langchain_core` forbidden in domain/application by
  import-linter (probe in PR body). `Ask.__call__` split into eight named
  stages in `application/ask.py` over a shared `AskDeps`; `AskState` TypedDict
  (stdlib, in `application/ask.py`) with `steps: Annotated[list[Step],
  operator.add]`; `build_ask_graph(deps)` in `adapters/graphs/ask_graph.py` —
  8 thin node closures, conditional refusal/ok edge, all three sections run
  every time so the fan-in at `evidence` runs exactly once (verified against
  the installed package: three 0.6 s sleeps in 0.63 s wall, 4 worker threads).
  Parity eval `evals/test_ask_graph_parity.py`: all 35 intent_cache cases
  through both paths, 17 fields equal + steps as set of triples, zero model
  calls. Concurrency test 20×4 threads over one temp SQLite file.
  `scripts/run_ask_graph.py` (`stream_mode="updates", version="v2"`;
  `--diagram` → `get_graph().draw_mermaid()`); AGENTS.md LangGraph paragraph
  appended verbatim; README architecture paragraph. `POST /ask` unchanged;
  prompts/schema/golden/thresholds/web untouched. Measured: 871 passed / 15
  skipped; eval 12 passed / 3 skipped (~12:45 with register). Review: APPROVE
  round 1, 0 must-fix (PR comment on #65); 1 nit applied pre-push (`dd033ee`,
  `kinds.count("evidence") == 1`). Follow-ups (not ticketed): ticket 16 owns
  the checkpointed-state restriction (`AskState` today carries non-JSON values
  like `page_texts` — fine while nothing is checkpointed); a live
  one-question run of `scripts/run_ask_graph.py` is pending (the endpoint was
  quota-exhausted on 2026-11-23).

- Ticket 16 — Conversation memory (issue #55, PR #68, squash `0818cfb`). Note:
  `docs/learning/16-conversation-memory.md`. `POST /chat` + `GET
  /chat/{thread_id}`; rewrite-then-parse keeps `INTENT_SYSTEM_PROMPT` and its
  captures untouched (contextualise = the fifth model call site, once per
  follow-up turn); total rewrite guard in `domain/chat.py` (≤500 chars, falls
  back to the raw message with a machine reason, disclosed to the client);
  history enters the prompt as data; refusals not launderable; one shared
  `InMemorySaver` (restart limit by design, `GET /meta` says
  `chat_memory: "in_memory"`); ≤ 50 turns under a module-level
  `threading.Lock` (the review's must-fix — the cap check raced across
  FastAPI threads; `InMemorySaver` is not internally locked); thread ids
  server-issued UUID4, client ids validated + canonicalised; page text kept
  out of checkpoints by compiling the ask subgraph `checkpointer=False`
  (verified against the locked langgraph source: subgraphs inherit the parent
  checkpointer through the run config) with a marker-scanning proof test.
  Eval: 13 conversations / 24 turns in `evals/chat_golden.yaml`; cache =
  12 rewrite (digest-keyed) + 31 intent payloads; replay zero-model; floors
  `followup_accuracy` 13/13 = 1.0 and `followup_refusal_recall` 2/2 = 1.0;
  enforcement logic proven without the cache by a synthetic fixture
  (`tests/test_chat_golden_logic.py`). Captures via the `LLM_MODEL` env
  fallback model. Measured: 923 passed / 18 skipped; web schema regenerated,
  22 Vitest tests green. Review: round 1 REQUEST_CHANGES (the cap race),
  round 2 APPROVE. Post-review capture findings (both fixed): the replay had
  wired `provider_limit=5` against the capture's served 20 — providers shown
  are part of the rewrite digest, so provider-bearing follow-ups missed the
  cache (fixed by sharing `build_capture_deps` between capture and replay +
  a regression test pinning `provider_limit == ASK_PROVIDER_LIMIT`); one
  golden expectation was stricter than the system's own combined
  filters+price convention (the rewrite correctly carries the place forward)
  — expectation corrected, floor untouched. Follow-ups (not ticketed): the
  chat golden replay still executes the register (21–25 min) although the
  floors are intent-level — ticket 17's first commit makes it intent-level
  (capture stores per-turn summaries; replay rebuilds history from the cache,
  zero SQL); a live `POST /chat` check through the running server is pending.

## In progress

- None. Ticket 16 merged (PR #68, squash `0818cfb`); **ticket 17 (issue #56) is
  next**, the first ticket run under the leaner-process decision of 2026-11-23
  (one PR per ticket, one review round, full Reviewer because the chat page is
  a new class of behaviour, plus the chat-eval intent-level replay as its
  first commit).

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

- **Ticket 17 — Chat page in the web front end (issue #56) is next**, then
  18–24 in number order (each depends on the previous). Ticket 16 is merged
  (Done entry below carries the details); ticket 17 runs under the
  leaner-process decision of 2026-11-23 and owns the chat-eval intent-level
  replay (its first commit).
- **LLM quota facts for the phase (2026-11-23).** The free-tier daily quota
  was exhausted again after ticket 14's captures + live demo: the native
  endpoint returns 429, and the OpenAI-compat surface returns an empty 404 in
  this state — when pacing live work, treat a 404 from
  `$LLM_BASE_URL/chat/completions` as "likely quota/unavailable" and check the
  native endpoint for the real status. Tickets that need live calls (captures,
  demos) should run early in the quota day; ticket 15 needs none.
- Optional, not ticketed (owner decisions needed):
  - The pending ~300-provider extraction run from ticket 08 (≈ $1.34 est.) —
    CAN run on this machine (it reaches Gemini); pace it after the daily
    free-tier quota resets, cache makes a rerun cheap.
  - Fill the ER sample verdicts (`evals/er_sample.yaml`) and the remaining 10
    judge verdicts — owner hand-labelling.
  - Langfuse keys for a live-tracing demo (owner adds to `~/.config/ndis/.env`;
    keys now present but `tracing.configured=false` at 2026-11-19 — check the
    env names `adapters/tracing.py` expects before demoing).
  - Snowflake trial expires ~2026-12-08; only relevant if the comparison is
    re-run.
  - Ticket 12 and ticket 14 review nits (non-blocking) recorded in the
    respective Done entries.
