# ndis-provider-navigator

Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2. Run Python with `uv run`. Front end (ticket 09): `web/` (React 19, TypeScript strict, Vite, Tailwind v4, pnpm); run with `pnpm --dir web <script>`.

Layers (enforced by import-linter): domain -> application -> adapters/api. `web/` talks to `api` over HTTP only, through the client generated from the OpenAPI schema. `domain` imports nothing from frameworks; `application` imports only `domain`. New external dependency: justify it in the PR body.

Before you finish any change run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports && uv run pytest`. Evals: `uv run pytest -m eval --no-cov` (the coverage gate applies to the unit/integration run only). The default `uv run pytest` deselects the `slow` marker (the register-backed `evals/test_ask_golden.py` and `evals/test_ask_graph_parity.py`); run the full eval gate once before pushing — CI enforces it again. If `web/` changed: `pnpm --dir web check` (typecheck, lint, format, test, build).

Never commit anything under `data/private/`. Never put model/vendor names outside `config.py` and env (the front end reads them from `GET /meta`). UI design decisions follow `web/design-system/MASTER.md`, generated with the `ui-ux-pro-max` skill. Never make an LLM output trusted without a deterministic check in `domain`.

Tests: pytest, fixtures in `tests/conftest.py`, LLM calls mocked via the `LLMClient` port; integration tests use a temp SQLite file. Evals live in `evals/` and are marked `@pytest.mark.eval`; the register-backed ones are also `slow` (deselected by the default run).

Commits: Conventional Commits. One ticket per branch `ticket/NN-slug`.

LangGraph (tickets 15–24): graphs live only in `src/ndis/adapters/graphs/`. A node is a thin wrapper around an `application` use case or stage function; no business rule lives in a graph module. Model calls go through the `LlmClient` port, never a LangChain model class. State that is checkpointed holds JSON-plain values and never page text. After changing a graph, regenerate its diagram in `docs/graphs/`; a test fails on drift.
