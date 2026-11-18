# 12 — Snowflake Cortex Search comparison

## What we built

A measured comparison of Snowflake Cortex Search against the local retrieval engine on the exact same corpus and the exact same golden questions. The 284 heading-strategy chunks of the price guide (270,829 characters) plus their 3072-dimensional embedding vectors were uploaded from the local `sqlite-vec` index into a personal Snowflake trial (`NDIS_DB.RULES.PRICING_CHUNKS`, with the vectors in a `VECTOR(FLOAT, 3072)` column), a Cortex Search service was created over that column, and each of the ten golden pricing-rule questions — plus the off-topic refusal case — was sent to both systems with the same cached query embedding. `scripts/snowflake_load.py` does the load and service creation; `scripts/snowflake_compare.py` runs the comparison and writes `reports/snowflake.md`.

The headline discovery: trial accounts cannot run Snowflake's managed-embedding functions (`AI function EMBED_TEXT_768 is not available for trial accounts.`), so the comparison uses user-provided embeddings — which makes it a *controlled* experiment: the embedding model is pinned across both systems, so the only variable left is the serving engine (Cortex Search's vector index and retrieval pipeline versus local `sqlite-vec` cosine search).

## Concepts

- **Managed vs user-provided embeddings.** Cortex Search normally embeds the corpus server-side (`EMBED_TEXT_*`, default model `snowflake-arctic-embed-m-v1.5`). Those AI functions are blocked on trial accounts. The alternative documented path is a `VECTOR INDEXES` column you embed yourself, queried with `multi_index_query: {"<column>": [{"vector": [...]}]}`.
- **Fair benchmarking = pin everything except the variable under test.** Same chunks (rebuilt with the same deterministic chunker), same vectors (read as raw bytes out of the local index, never re-embedded), same query embeddings (one cached embed per question, sent to both systems). Measured quality differences then isolate the engine, not the embedder.
- **hit@3 as the quality metric:** the fraction of golden questions whose expected printed page appears in the top-3 retrieved pages, identical to the definition `reports/retrieval.md` uses, so the numbers are comparable across tickets.
- **Key-pair authentication.** A service user with an RSA public key on the user object; the private key stays on the machine and the connector signs a JWT per session. No password exists to leak, and the role (`NDIS_ROLE`) scopes the user to the objects it needs.
- **Serverless indexing vs warehouse compute.** `TARGET_LAG = '1 day'` keeps the index fresh through Snowflake-managed refreshes; the warehouse only runs the source query at create/refresh time. Serving happens on Snowflake's infrastructure, not your warehouse.
- **Refusal is application policy, not engine behaviour.** A top-k retrieval engine always returns its top-k — Cortex Search happily returns chunks for "What is the capital of France?". The local system refuses below a measured similarity threshold (0.65); anything built on a managed search service has to implement that gate itself, around the service.
- **Cost from metering views, not vibes.** `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` records per-second credit consumption; XSMALL is 1 credit/hour, and with a 60 s auto-suspend the whole experiment cost a fraction of one credit.

## Decisions and why

- **User-provided vectors instead of upgrading the account.** The trial cannot run `EMBED_TEXT_*`, so managed embeddings were impossible. BYO vectors were the only Cortex Search path available — and they make the comparison cleaner anyway, because identical embeddings on both sides mean any quality difference would be the engine's, not the embedder's.
- **Vectors read from the local index, not re-embedded.** The `sqlite-vec` file stores the unit-normalised vectors the local system actually serves; unpacking and uploading those bytes guarantees both indexes hold the same vectors and costs zero additional embedding quota.
- **`SEARCH_PREVIEW` over a new SDK dependency.** The multi-index query syntax exists only in the SQL and Python APIs; the Python API would have pulled in Snowpark (and PyArrow) for one script. `SNOWFLAKE.CORTEX.SEARCH_PREVIEW` through the connector is documented for exactly this preview use; the report states honestly that it is not the production serving path and carries higher latency.
- **A dedicated key-pair service user, not the personal login.** The browser login stays for humans; scripts get `NDIS_SERVICE` with a role that can touch only the trial objects it needs. Nothing with a password exists, and the private key never enters the repository or the environment file's values beyond a path.
- **Only the served chunking strategy is uploaded.** The local index holds `fixed` and `heading` chunks; production answers from `heading`, so Snowflake gets `heading` only — half the rows, no duplicated index cost, and the comparison measures what would actually be served.
- **`CREATE OR REPLACE` on every load.** Re-running the loader re-indexes from scratch instead of leaving a stale index; atomicity is guaranteed by the statement, and the corpus is tiny (sub-second source query).

## How to say it in an interview

"I benchmarked a managed search service against our in-process vector store without fooling myself: the corpus and embedding vectors were byte-identical on both sides, so the only variable was the serving engine. Both hit 10/10 on the golden set with the same top-3 ordering — the interesting differences were operational, not quality: the managed service always returns its top-k (the refusal threshold is my application's job, not the engine's), its query round trip from my machine was ~200 ms versus ~6 ms in process, and the trial account wouldn't run the managed-embedding functions at all, which I discovered from the error message and designed around with user-provided vectors. I also measured the cost honestly from the account's metering history — 0.2 credits for the whole session — instead of guessing."

## Check yourself

1. Why upload the embedding vectors instead of letting Snowflake embed the corpus?
2. Why does Cortex Search return chunks for the off-topic question while the local system refuses it?
3. Why read the vectors out of the local index rather than re-embedding the chunks for Snowflake?

**Answers:** 1. On this trial account the managed-embedding AI functions are refused (`EMBED_TEXT_768 is not available for trial accounts`), and pinning the embedder on both sides makes the comparison a pure serving-engine measurement — any quality difference would otherwise be confounded by the embedding model. 2. A top-k retrieval engine has no "not in corpus" concept: it always returns its best k. The local refusal is an application-layer similarity threshold (0.65, measured in reports/retrieval.md) that sits between retrieval and the LLM; a managed service requires rebuilding that gate around the service. 3. The local index stores exactly the unit-normalised vectors the local system serves, so reading them out costs no embedding quota, is deterministic, and guarantees the two indexes hold the same bytes — re-embedding would add cost and the risk of a silently different corpus.

## Numbers

- Corpus: 104 PDF pages → 284 heading chunks, 270,829 characters, 3072-dim vectors; 284 rows uploaded, index built server-side in under a minute.
- Quality: hit@3 local 10/10 (100%), Snowflake 10/10 (100%); identical top-3 page ordering on every golden question.
- Off-topic: local top-1 similarity 0.620 → refused below the 0.65 threshold; Cortex Search returned pages 72, 34, 71 (no refusal gate).
- Latency (from this machine, ap-southeast-2): local `sqlite-vec` search ≈ 5-7 ms median; Snowflake `SEARCH_PREVIEW` round trip ≈ 200-300 ms median across runs (180-725 ms observed), excluding warehouse-resume time.
- Cost: 0.1994 credits on `NDIS_XS` (≈ 12 minutes of XSMALL uptime) + 0.0003 credits cloud services for the whole session; trial balance still showed $400 of $400 left after the run.
- Checks: 741 passed, 1 skipped, coverage 95.80%; eval run 8 passed, 1 skipped (entity-resolution labels not filled); no-stubs and private-data guards passed.
- Measured 2026-11-10 on the personal trial `wt38369` (ap-southeast-2); the trial expires 28 days from 2026-11-10.
