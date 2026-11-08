# 00 — Repo, tooling, CI, protections

## What we built

An empty Python project where every quality rule is already enforced by a machine, before any feature code exists. The repo has the five architectural layers as empty packages, a `pyproject.toml` that configures the linter, formatter, strict type checker, import-rule checker and test runner, two small guard scripts (one that refuses half-finished code, one that refuses private data), and a GitHub Actions job that runs all of it on every pull request. `main` only accepts pull requests whose CI is green. We proved the gate works by opening a second pull request with a deliberate stub and watching it go red.

## Concepts

- **Hexagonal layers (ports and adapters):** `domain` holds business rules and knows nothing about frameworks; `application` orchestrates use cases through interfaces; `adapters`, `api` and `ui` plug real things (SQLite, HTTP, Streamlit) into those interfaces. In Android terms: `domain` is your pure Kotlin module, `adapters` are the Room/Retrofit implementations. Visible in `pyproject.toml` `[tool.importlinter]` and the `src/ndis/*` packages.
- **import-linter contract:** a build-time rule that fails if a lower layer imports a higher one or a framework. It is the Python equivalent of Gradle module boundaries, except enforced by scanning imports. `pyproject.toml`, contracts "Outer layers depend inward" and "Domain and application have no external dependencies".
- **`mypy --strict`:** optional static typing made mandatory for `domain/` and `application/`, where mistakes are most expensive. Think Kotlin's type system switched on for the parts of Python that matter. `[tool.mypy] files = [...]`.
- **Coverage gate:** `pytest --cov ... --cov-fail-under=80` fails the test run if less than 80% of the inner layers' lines executed during tests. `[tool.pytest.ini_options] addopts`.
- **Lockfile:** `uv.lock` pins the exact version and hash of all 70 packages so every machine installs the same thing, like Gradle's `gradle.lockfile`. Ours is produced by CI (see Decisions).

## Decisions and why

- **Public repo with required status checks, no approving review.** GitHub's free plan has no branch protection on private repos, and there is no second account to approve. Alternative rejected: paying for Pro or pretending a self-approval is a review. The independent Reviewer agent posts `APPROVE` as a PR comment instead; that is the audit trail.
- **CI resolves the lockfile against PyPI and fails if the committed lock differs.** The dev Mac can only reach a corporate package mirror (and that mirror ships a patched `certifi` that is not on PyPI), while CI can only reach PyPI. A lockfile can hold one set of URLs. Alternatives rejected: committing the mirror lock (CI cannot download; leaks an internal hostname into a public repo), rewriting URLs with `sed` (breaks on the patched package), not committing a lock at all (non-reproducible builds). The lock is kept out of local git with `git update-index --skip-worktree uv.lock`.
- **Layer contract uses `:` not `|` between `api`, `ui` and `adapters`.** The Reviewer caught that `|` makes them independent siblings, so the FastAPI composition root could never import the adapters it has to wire. `:` keeps them in one outer layer that still cannot import upward.
- **Eval tests run with `--no-cov`.** Otherwise a 10-test eval subset would fail on coverage, not on evals. Ticket 08 owns the eval gate.
- **`.gitignore` globs anchored to the repo root (`/*.csv`).** Global `*.csv` would have silently ignored test fixtures and labelled eval datasets that later tickets must commit.

## How to say it in an interview

"Before writing a line of feature code I set up the guardrails as CI gates: ruff for lint and format, mypy strict on the domain and application layers, import-linter contracts that make the hexagonal architecture a build failure rather than a convention, an 80% coverage floor on the inner layers, a script that rejects stubs and TODOs, and a guard that refuses to commit private data. Then I proved the gate had teeth by opening a PR with a deliberate `NotImplementedError` and watching it go red. The interesting problem was the lockfile: my dev machine resolved through a corporate mirror with a patched `certifi`, CI could only see PyPI, so I made CI the single source of truth for `uv.lock` — it regenerates the lock, publishes it as an artifact and fails if the committed one differs. An independent reviewer agent caught three real defects in my own tooling before merge."

## Check yourself

1. Why does `domain/` forbid importing `pydantic` when Pydantic is the project's validation library?
2. What breaks if `api`, `ui` and `adapters` are declared as independent siblings (`|`) in import-linter?
3. Why can't the lockfile written on the dev Mac simply be committed?

## Numbers

- 13 tests, 0.34 s locally; inner-layer coverage 100% (0 statements — nothing to measure yet).
- CI `quality` job: 12 s on the green run, 13 s on the deliberate-stub run.
- 70 packages locked against PyPI, 2 hosts in the lock (`pypi.org`, `files.pythonhosted.org`).
- 3 review rounds: 4 must-fix findings in round 1, 0 in rounds 2 and 3.
- Green run: <https://github.com/jdhc3m/ndis-provider-navigator/actions/runs/42523418019>. Deliberate-stub red run (PR #17): <https://github.com/jdhc3m/ndis-provider-navigator/actions/runs/42523437994>.

---

Answers: 1. The domain must stay portable and framework-free so its rules can be tested and reused without any library; Pydantic models live at the edges (`api`, `adapters`) and are converted to plain domain objects. 2. The FastAPI composition root in `api` cannot import the SQLite repositories in `adapters` to wire them, so the app cannot be assembled without breaking the contract. 3. It records the mirror's URLs and a patched `certifi` version that do not exist on PyPI, so CI cannot download anything and an internal hostname would land in a public repo.
