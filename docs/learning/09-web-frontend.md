# 09 — Web front end in React/TypeScript

## What we built

A single web page in `web/` (React 19, TypeScript strict, Vite, Tailwind CSS v4, pnpm) that shows everything the service can prove about an answer: the parsed intent as code chips, provider cards with their registration badge and a verbatim evidence quote per service tag, the price limit straight from the catalogue, the pricing-rule answer with page citations, a refusal banner when the system declines, and a "How this answer was built" table with the time, tokens and cost of every step. The page talks to FastAPI only over HTTP, through a TypeScript client generated from the API's own OpenAPI schema, and FastAPI serves the built page at `/`. Two small backend pieces made that possible: `GET /meta` (data dates and model name, so nothing in `web/` names a vendor) and a `steps` list on `POST /ask` recorded by the use case itself. The never-built Streamlit package is gone.

## Concepts

- **Presentation boundary:** the front end is outside `src/ndis/` and can only reach the service through HTTP, like an Android app talking to a backend. Visible in `web/src/api/client.ts` (the only place that calls `fetch`) and the import-linter contract in `pyproject.toml`, which no longer mentions a `ui` layer at all.
- **Typed API contract from one source of truth:** `scripts/export_openapi.py` dumps FastAPI's schema, `openapi-typescript` turns it into `web/src/api/schema.d.ts`, and CI regenerates and diffs it. Change a Pydantic model without regenerating and the `web` job fails — the Kotlin analogue is a generated Retrofit client from an OpenAPI spec checked into the repo.
- **Design tokens:** the `ui-ux-pro-max` generator wrote `web/design-system/MASTER.md`; its palette, type scale and spacing became CSS variables in `web/src/styles/tokens.css` and Tailwind reads them through `@theme`. Components never contain a raw hex colour, the way Android components reference `?attr/colorPrimary` rather than `#0369A1`.
- **Accessible states as data:** idle, loading, answered, refused, empty and error are explicit states in `web/src/App.tsx`, each with its own landmark and ARIA role (`aria-busy`, `role="status"`, `role="alert"`), not CSS toggles. Tests drive them through roles and names, so they double as an accessibility check.
- **Build-time dead code:** mock mode lives behind `import.meta.env.VITE_MOCK_API === '1'` and a dynamic import in `web/src/api/index.ts`; the production bundle contains none of the fixtures. Same idea as a `debug` build flavour that strips a fake backend from `release`.

## Decisions and why

- **React + TypeScript instead of Streamlit.** Streamlit would have hidden the HTTP boundary and the typed contract; a TypeScript page shows an engineer-grade front end and lets CI prove the API and the page agree. Rejected: Next.js/SSR (nothing to render on a server) and a component library beyond Tailwind (hand-written components keep the token discipline visible).
- **The use case records its own steps.** Timing and token counts are captured inside `Ask.__call__` with an injectable clock; cost is computed in the API layer from prices the composition root injects, so `application` still imports nothing from `config`. Rejected: reconstructing steps from Langfuse traces — that would make the page depend on an optional external service.
- **`register_snapshot_date` comes from the register CSV's mtime, never the SQLite file.** SQLite rewrites the database's mtime on every write, so the first implementation reported today's date; the fix reads only `data/public/register.csv` and returns `null` when it is absent.
- **Mock mode and fixtures on this machine, live `/ask` on the other.** The hosted LLM endpoints are blocked on the development Mac, so the answer, refusal, empty and error states were built, tested and screenshotted from typed fixtures that match the generated schema; `/`, `/meta`, `/providers` and CORS were exercised live. Rejected: adding a replay mode to the API just for screenshots.
- **Schema-diff check in the `web` CI job, not `quality`.** The artefact it protects is `web/src/api/schema.d.ts`, consumed by the typecheck in the same job; the cost is installing uv there too.
- **`pnpm-lock.yaml` is committed normally** (integrity hashes only, so the corporate mirror and npmjs agree), unlike `uv.lock`, which CI still regenerates.

## How to say it in an interview

"The front end is a deliberately thin React and TypeScript page whose only job is to make the grounding inspectable. Every provider card shows the registration badge from the Commission register and, for each service tag, the verbatim quote from the provider's website with its URL and fetch date; the price comes from the catalogue with a 'not generated' pill; the rule answer shows page citations; and a disclosure at the bottom lists every step the service ran with its milliseconds, tokens and cost. The page can't drift from the API because its client types are generated from the FastAPI OpenAPI schema and CI fails if the committed types differ. Design decisions came from a generated design system turned into CSS tokens, so contrast, focus rings, 44-pixel targets and reduced motion are properties of the tokens, not of individual components. Nothing in the web code names a model; it reads that from `/meta`. And FastAPI serves the built page itself, so the demo is one process."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: how does the web page talk to the Python service, and what is it not allowed to do?
2. Where does the page get the model name and data dates it shows in the header, and why not hard-code them?

**Spot it**

3. Which file is the only one that calls `fetch`, and which generated file gives it its types?
4. Which CI step fails when a Pydantic response model changes but the TypeScript schema was not regenerated?

**Defend it**

5. Why generate `schema.d.ts` from the API instead of writing the TypeScript types by hand?
6. Why did the first `/meta` report the wrong register date, and what is the general lesson?
7. Why must the mock mode be absent from the production bundle rather than just switched off?

**Answers:** 1. Over HTTP only, through a typed client generated from the FastAPI OpenAPI schema; `web/` lives outside `src/ndis/` and cannot import Python, exactly like an Android app and its backend. It never computes an answer itself — it renders what `/ask` returns, including refusals. 2. From `GET /meta`. Vendor and model names are allowed only in `config.py` and env, so swapping Gemini for OpenAI changes nothing in `web/`. 3. `web/src/api/client.ts`; its request and response types come from `web/src/api/schema.d.ts`, generated by `openapi-typescript` from `/openapi.json`. 4. "Generated API schema must match the committed one" in the `web` job of `.github/workflows/ci.yml`: it runs `scripts/export_openapi.py` and `pnpm gen:schema`, then fails on any diff. 5. Hand-written types are a second source of truth that silently drifts when a Pydantic model changes; generating them and diffing in CI makes the API the only source and turns drift into a red build. 6. It used the SQLite file's mtime, which SQLite rewrites on every write; the lesson is that "the date of the data" must come from the artefact that *is* the data (the downloaded register file), not from a derived store. 7. Switched-off code still ships fixtures (fictitious providers, URLs, a fake model name) to users and bloats the bundle; a build-time guard with a dynamic import makes the fixtures physically absent, which the review verified by grepping `web/dist`.

## Numbers

- Python: 667 tests passed, 6 skipped (data-backed evals), coverage 95% on `domain/` + `application/`; `uv run pytest -m eval --no-cov` 3 passed, 6 skipped.
- Web: 16 Vitest tests (12 page-state tests, 4 client tests); production bundle 247 kB JS (76 kB gzipped); zero external requests.
- Contrast of every text pair used ≥ 5.17:1 (computed in review); `web` CI job 50 s; `quality` 36 s.
- Live on the Mac: `GET /providers?postcode=4101&limit=2` 0.13–0.16 s; `/ask` returns 503 "LLM not configured" (endpoint blocked), shown in the page's error state.
- Review: 0 must-fix items, 8 optional nits; the in-scope ones landed in one follow-up commit before merge, the rest are listed as follow-ups in `docs/harness/STATE.md`.
