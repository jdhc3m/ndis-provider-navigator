# NDIS Provider Navigator

A portfolio project for grounded NDIS provider discovery and deterministic pricing
in Brisbane. **This repository currently contains tooling only, not a working
provider navigator.** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Streamlit and uv.

## Development

```sh
uv sync --locked
uv run pre-commit install
uv run ruff check . && uv run ruff format --check .
uv run mypy && uv run lint-imports && uv run pytest
uv run python scripts/check_no_stubs.py
uv run python scripts/check_private_data.py --tracked
```

Run `uv run pytest -m eval` once model-backed evaluation tests are added. CI gives
a visible notice when the suite or a usable model/key is unavailable. No LLM key
is required for tickets 00–04. Personal model configuration belongs in
`~/.config/ndis/.env` (see `.env.example`); environment-file loading is not built yet.
Never use workplace credentials.

## Architecture

- `src/ndis/domain/`: pure entities and rules, no frameworks.
- `src/ndis/application/`: ports and use cases, depends inward on domain.
- `src/ndis/adapters/`: database, HTTP, model and tracing implementations.
- `src/ndis/api/`: FastAPI composition; `src/ndis/ui/`: Streamlit page.

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

Register and catalogue ingestion; provider and pricing endpoints; entity
resolution; polite website fetching; evidence-backed extraction; rule retrieval;
intent parsing and refusal guards; measured model evaluations; tracing; Streamlit;
geographic ranking; and the fresh-clone demo. No provider, service, pricing or rule
answer should be considered available at this stage.
