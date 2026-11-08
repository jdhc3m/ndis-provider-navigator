# Foreman resume state

Updated: 2026-11-08 (Australia/Brisbane).

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
- `pytest -m eval` runs with `--no-cov` in CI: the coverage gate applies to the
  unit/integration run, not the eval subset. Ticket 08 owns the eval gate design.

## Done

- None merged yet.

## In progress

- Ticket 00 — repo, tooling, CI, protections (issue #1): PR open. The learning
  note `docs/learning/00-repo-tooling.md` is written after merge.

## Blocked

- None.

## Next

- Ticket 01 — register ingestion (issue #2).
