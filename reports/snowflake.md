# Snowflake Cortex Search comparison (ticket 12)

Source: NDIS Pricing Arrangements and Price Limits 2025-26 v1.1, chunked with the served heading strategy (10 golden questions plus the off-topic refusal case).
Corpus and vectors: the 3072-dim vectors read out of the local `sqlite-vec` index, uploaded to Snowflake; the service holds the identical chunk texts and embedding vectors, so both systems search the same index.

## Account and privacy controls

- Personal trial account `wt38369.ap-southeast-2`; no workplace accounts or credentials.
- Connection: key-pair service user `NDIS_SERVICE` (RSA public key on the user, private key only on this machine), role `NDIS_ROLE`, warehouse `NDIS_XS` (XSMALL, auto-suspend 60 s). The user has no password and no MFA; it can only reach the trial objects its role is granted.
- Data uploaded: the price-guide chunk texts (public document, `data/public/pricing_arrangements.pdf`) and their embedding vectors. Nothing from `data/private/` (fetched pages, Maps cache, judge verdicts, cost logs) was uploaded.
- Destructive steps are scoped to the `NDIS_DB.RULES` schema of the trial account; `CREATE OR REPLACE` re-indexes the service, nothing else is touched.

## Reproducible steps

```sh
set -a; source ~/.config/ndis/.env; set +a
uv run --extra snowflake python scripts/build_rule_index.py  # local index + cache
uv run --extra snowflake python scripts/snowflake_load.py \
    --pdf data/public/pricing_arrangements.pdf --index data/rule_index.db
uv run --extra snowflake python scripts/snowflake_compare.py
```

## Why user-provided embeddings

Trial accounts cannot run the managed-embedding AI functions: creating a service that would let Snowflake embed the corpus fails with
`AI function EMBED_TEXT_768 is not available for trial accounts.`
The comparison therefore pins the embedding model (the cached client behind the local index) for both systems and measures the serving platforms - Cortex Search's vector index plus its retrieval pipeline versus local `sqlite-vec` cosine search. A paid account could additionally test managed embeddings; that is out of scope here.

Query surface: `SNOWFLAKE.CORTEX.SEARCH_PREVIEW` with the multi-index query syntax (user-provided vector embeddings are not queryable through the REST API); the docs note SEARCH_PREVIEW carries higher latency than the production REST/Python APIs. Snowflake numbers are medians over 3 measured runs after one warm-up call (warehouse resume is paid once, not per query).

## hit@3 over the ten golden questions

| question | expected page | local top-3 | local top-1 sim | snowflake top-3 | snowflake ms (median) | local hit@3 | snowflake hit@3 |
|---|---|---|---|---|---|---|---|
| Can a provider claim 100% of the agreed fee for a short noti | 26 | 26, 26, 27 | 0.784 | 26, 26, 27 | 725 | yes | yes |
| How many clear business days notice does a participant need  | 26 | 26, 26, 27 | 0.811 | 26, 26, 27 | 497 | yes | yes |
| Can a provider charge for the time spent travelling to a par | 22 | 21, 22, 28 | 0.780 | 21, 22, 28 | 244 | yes | yes |
| Can telehealth be used to deliver supports and claimed, and  | 20 | 20, 32, 20 | 0.843 | 20, 32, 20 | 355 | yes | yes |
| Can a provider claim for time spent on non-face-to-face acti | 21 | 21, 20, 37 | 0.765 | 21, 20, 37 | 286 | yes | yes |
| Are registered providers allowed to add extra charges such a | 40 | 40, 9, 38 | 0.771 | 40, 9, 38 | 409 | yes | yes |
| Are NDIS supports GST-free? | 40 | 40, 4, 14 | 0.877 | 40, 4, 14 | 237 | yes | yes |
| How much higher are price limits in remote and very remote a | 31 | 30, 31, 9 | 0.811 | 30, 31, 9 | 227 | yes | yes |
| Do the price limits apply to plan-managed participants? | 10 | 9, 10, 10 | 0.753 | 9, 10, 10 | 281 | yes | yes |
| Can a provider claim the costs of running and maintaining a  | 34 | 34, 17, 11 | 0.751 | 34, 17, 11 | 244 | yes | yes |

**Local hit@3: 100%** (10/10). **Snowflake hit@3: 100%** (10/10).

## Off-topic refusal behaviour

Local: the "What is the capital of France?" case refuses - top-1 similarity 0.620 sits below the 0.65 threshold, no model call is made.
Snowflake: Cortex Search always returns its top-3 (pages 72, 34, 71); there is no similarity threshold, so the refusal gate the local system enforces would have to be re-implemented around the service.

## Latency

- Local `sqlite-vec` search (in process, embeddings excluded): median 5.2 ms per query.
- Snowflake `SEARCH_PREVIEW` round trip (embedding excluded, vector sent): median 283 ms per query (min 227, max 725) from this machine to the ap-southeast-2 region, through the SQL surface.

## Cost

Measured on the account usage views and the Snowsight UI after the 2026-11-10 run (session includes the setup attempts, two service re-indexings and the comparison queries):

- `NDIS_XS` warehouse: **0.1994 credits** billed in the 24 hours of the session - about twelve minutes of XSMALL uptime at 1 credit/hour (load, indexing and all queries; the account suspends it 60 s after each burst).
- Cloud services (compilation, metadata): **0.0003 credits**.
- Trial credit balance at report time: **$400 of $400 left** - the trial credits had not been debited in the UI view yet; the metering history above is the honest consumption signal.
- Embeddings: zero extra cost - the vectors were already cached on disk from the local index build; the golden queries were cache hits.

## Honest limitations

- `SEARCH_PREVIEW` is the SQL preview surface, not the production REST/Python API; the measured Snowflake latency is an upper bound on the serving path.
- The latency numbers come from one machine over residential internet; they include client-side network, not just service time.
- The corpus is small (284 chunks, one document); at this size retrieval quality differences between engines are dominated by the shared embeddings.
- The reranker behaviour of the service was not independently verified; the query used the default scoring.
- The trial account expires in 28 days from 2026-11-10; the service, table and key must be recreated after that.

