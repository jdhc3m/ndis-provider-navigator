# Foreman resume state

Updated: 2026-11-08 (Australia/Brisbane).

A new Foreman reads this file, `gh issue list --label ticket --state open`, and
`gh pr list`, finishes any open PR first, then continues with the lowest-numbered
open issue whose dependencies are closed. The full brief is
`/Users/M057692/NDIS/HARNESS_PROMPT.md`; read it before resuming.

## Environment facts the next Foreman needs

- Work on Jackson's Mac, not in an orb. The Mac sits behind Zscaler: `uv sync`
  only resolves while the **Staff VPN** is connected (Artifactory PyPI mirror).
  Gemini/OpenAI endpoints are blocked on this laptop, so tickets 05–08 and the live
  demo need another machine for the LLM-backed parts. Never bypass the proxy or
  disable TLS.
- Use `GH_HOST=github.com` for every `gh` call; the Mac otherwise defaults to an
  expired Westpac Enterprise login. Account is `jdhc3m` (free plan).
- The repo is **public**. `main` protection uses required status checks only
  (`quality` job); no approving review is required because there is no second
  account. The Reviewer subagent's `APPROVE` is posted as a PR comment and is the
  approval record.
- Private inputs (`/Users/M057692/NDIS/ndis.db`, `ndis-providers-brisbane*.csv`,
  `google-maps-scraper-kit/`, `.foreman*`) live in the parent directory, outside
  the repo. `git log --all -- data/private` must stay empty.
- Canonical checkout: `/Users/M057692/NDIS/ndis-provider-navigator` (`main`).
  Ticket worktrees: `/Users/M057692/NDIS/ndis-provider-navigator-ticket-NN`.

## Done

- Ticket 00 — repo, tooling, CI, protections: issue #1. Toolchain, layer contracts,
  stub guard, private-data guard, CI workflow, README skeleton, learning-note
  directories. Learning note: `docs/learning/00-repo-tooling.md`.

## In progress

- None.

## Blocked

- None.

## Next

- Ticket 01 — register ingestion (issue #2).
