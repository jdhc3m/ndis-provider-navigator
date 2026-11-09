# Foreman resume state

Updated: 2026-11-09 (Australia/Brisbane), after ticket 03 merged; this Foreman thread continues with ticket 04.

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

## In progress

- Ticket 04 — website fetcher (issue #5): next.

## Blocked

- None.

## Next

- Ticket 04 (website fetcher) can run on this Mac; seed from the `pages` table
  of `data/private/maps_brisbane.db` first, live fetch only for the gaps.
  Likely new dependencies `httpx` (already a dev dep) and `trafilatura`
  (already on the import-linter denylist).
- Ticket 05 onward needs an LLM key and a machine that can reach the API;
  stop and tell the owner before starting 05.
