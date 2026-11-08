# Foreman resume state

Updated: 2026-11-08 (Australia/Brisbane). **Stopped on ticket 00: external blockers.**
No tickets merged. No hand-off thread created: fewer than three tickets processed.

## Exactly what Jackson must do next

1. Upgrade the personal GitHub account `jdhc3m` to **GitHub Pro** (or another plan
   supporting protected branches in private repositories). Keep this repo private.
   GitHub rejected the protection API with HTTP 403: "Upgrade to GitHub Pro or
   make this repository public to enable this feature." The Foreman must not
   change visibility or bypass protection to proceed.
2. Arrange a **different GitHub user with collaborator access** to approve PRs.
   Only `jdhc3m` is currently a collaborator. A Task review posted as a comment
   is useful but is not GitHub's required approving review; PR authors cannot
   approve their own PRs. Have that user accept the invitation and review the
   ticket PR after checks pass. Do not share their credentials with the agent.
3. Restore **approved Python package access on this Mac**: the configured
   Artifactory index fails its TLS connection and PyPI is denied with HTTP 403
   by Zscaler. Have the network/package policy fixed through the approved route;
   do not disable TLS verification or bypass the proxy. The required packages
   are not in the local cache. `uv sync` must succeed before resuming validation.
4. Then tell this Foreman: **"Resume ticket 00 from docs/harness/STATE.md; package
   access and private branch protections are available, and an independent PR
   approver is arranged."** No model key is needed until ticket 05.

## Repository and local state

- Private GitHub repo: <https://github.com/jdhc3m/ndis-provider-navigator>.
- Canonical checkout: `/Users/M057692/NDIS/ndis-provider-navigator`, branch `main`.
- Ticket 00 worktree: `/Users/M057692/NDIS/ndis-provider-navigator-ticket-00`,
  branch `ticket/00-repo-tooling`. All tooling work is here, not on `main`.
- The ticket scaffold is saved in a **local, unpushed commit**. There is no PR;
  do not push it as accepted work before completing validation and Task review.
- `main` has only an empty initialization commit; no feature or tooling bypassed
  PR review. GitHub confirms `main` is **not protected**. Do not merge until fixed.
- This state is maintained in the ticket branch; an untracked local entry point in the
  canonical checkout makes the resume instructions easy to find before merge.
- Original full brief: `/Users/M057692/NDIS/HARNESS_PROMPT.md`. Read it in full.
- Current conversation:
  <https://westpac.ampcode.app/threads/T-01a1bb0a-9a59-7040-adeb-81a9991b6255>.

## Done

- Verified Python 3.12, uv, Git and authenticated personal GitHub access.
- Created private repo and the initial empty `main`, plus ticket 00 worktree.
- Created all issues 00–12 with context, scope, exclusions, verifiable acceptance
  criteria, files, checks, learning topics, `ticket` and `day-N` labels.
- Ticket 00: <https://github.com/jdhc3m/ndis-provider-navigator/issues/1>, labelled
  `blocked`. Issues 01–12 are #2–#13, all open, not started.
- Drafted the tooling skeleton: exact AGENTS.md, ignored input data/env files,
  package-by-layer layout, quality configuration, pre-commit and CI private-data
  guards, no-stubs/API reference checker and tooling tests, CI workflow, README.
- Standard-library smoke checks passed: Python syntax compilation, TOML parsing,
  exact AGENTS comparison against the brief, three synthetic stub rejections,
  a valid implementation accepted, and real temporary Git-index rejection of
  synthetic private files in both staged and full-index modes. Both repository
  guard commands passed. These are **not** the required pytest/coverage checks.
- No raw register data, Maps exports, database or page content was staged.

## In progress / not done

- Ticket 00 is **not accepted**. Dependencies could not be installed, so there is
  no generated `uv.lock` and no claim that ruff/mypy/import-linter/pytest pass.
- CI must stay fail-closed (`uv sync --locked`); generate and commit `uv.lock`
  after package access is restored, rather than removing the lock check.
- Formatting, all required checks, independent Task review, PR CI, the deliberate
  red stub scratch PR and branch protection still need completion. The scaffold
  has not been formatted or reviewed and must be treated as work in progress.
- The required command chain was attempted with offline resolution after the
  network failures; `uv run ruff check .` failed before running ruff because
  dependencies were unavailable. No later command in that chain ran.
- No ticket learning note yet: the harness requires it **after merge**. Capture
  the red scratch-run URL in note 00 and append LEARNING_LOG only after merging.
- Tickets 01–12 depend on the unaccepted foundation; do not start feature work.

## Resume protocol and repo learnings

1. Read this state, the full brief, `AGENTS.md`, the open `ticket` issues and PRs.
   Finish any existing ticket PR before opening another.
2. Run GitHub commands with **`GH_HOST=github.com`**: this Mac otherwise defaults
   to an expired `westpac.ghe.com` account. Do not use workplace credentials.
3. The shell has `UV_INDEX_URL` and `PIP_INDEX_URL` pointing at Artifactory, plus
   proxy variables pointing at localhost:9000. Public-index override was tested
   and returned Zscaler 403; offline resolution also failed. No global settings
   were changed. Fix access legitimately rather than trying more bypasses.
4. `rg` is unavailable; use scoped `grep`/`find`. Run commands from the ticket
   worktree. `uv init` picked its folder name; pyproject already corrects it to
   `ndis-provider-navigator` with build module `ndis`.
5. Source register exists at `~/.amp/in/ndis/register.csv`. Existing private Maps
   exports, database and scraper remain in `/Users/M057692/NDIS`, outside the repo.
6. `~/.config/ndis/.env` does not exist and the shell key is unset. This is not a
   blocker for 00–04. Ask for personal model setup **only at ticket 05**.
7. Retry the main protection API only after the plan is fixed: require the
   `quality` status, up-to-date branch, one approving review, stale approval
   dismissal, resolved conversations and enforce administrators; disallow force
   pushes/deletion. Verify the installed protection before merging.
8. Follow the harness Builder/Reviewer loop; ticket 00 is the Foreman's tooling
   exception. Do not create another thread until the three-ticket hand-off.
9. Merge only when all acceptance criteria, CI and an actual independent approval
   are satisfied. Add the one-page six-section learning note via a reviewed docs
   change rather than bypassing protected `main`. Post the two-line progress
   message only after a merge. Count ticket 00 toward the three-ticket limit.
