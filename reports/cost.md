# Cost, latency and judge agreement
Generated 2026-11-09 14:15 UTC by `scripts/cost_report.py` from the private run recordings (`data/private/cost/`, not committed). Prices are the published paid-tier list prices per 1M tokens in `src/ndis/config.py` (checked 2026-11-09); the runs themselves went through the free tier, so the dollars are what this usage would cost at list prices, not what was paid. Model ids stay in `config.py` and the environment — endpoints are named by the pipeline step that calls.

## Measured per-call latency and tokens

Real calls recorded on 2026-11-09 with the lighter fallback model. Seconds are the client's own successful-POST latency — pacing waits and retry backoffs excluded — nearest-rank percentile over the dedicated endpoint-measurement runs; the n is small (3-4 calls per step), so treat p95 as indicative.

| pipeline step | n | input tokens (mean) | output tokens (mean) | p50 s | p95 s |
|---|---|---|---|---|---|
| Intent parse (POST /ask) | 3 | 488 | 73 | 1.3 | 1.8 |
| Rule judge + answer (POST /ask-rule) | 3 | 2218 | 213 | 1.7 | 2.6 |
| Question embeddings (retrieval gate) | 2 | 106 | 0 | 0.7 | 2.2 |
| Service extraction (per page) | 3 | 2846 | 178 | 1.4 | 1.6 |

## Cost over the golden set

| endpoint | calls | basis | cost USD |
|---|---|---|---|
| intent parse over the 17 golden questions | 17 | estimated: 17 × the measured per-call mean (488 in / 73 out) | $0.0056 |
| rule judge: the verdict-template generation run plus the latency re-measure (20 of the calls served the template's answers; the rest were refused candidates, which are real calls too) | 50 | measured | $0.0478 |
| service extraction over the ten labelled sites (the 2026-11-09 run, 46 page calls) | 46 | measured (88420 in / 7208 out tokens in the cache) | $0.0484 |
| pending full extraction run (~300 providers) | — | estimated: per-provider mean (9.6k tokens) × provider count at the same measured per-call basis | $0.8607 |

Embeddings: the recorded embedding calls cost $0.0001 measured (24 batch calls; cache hits replay for nothing). The index build over the whole priced-rules corpus is estimated at 135441 tokens from 541766 recorded chunk characters, the same deterministic estimate the adapter falls back to.

## What the eval gate itself costs

Nothing at run time. `pytest -m eval --no-cov` replays committed captures: the /ask golden eval replays `evals/intent_cache.yaml`, the intent-accuracy eval replays the same payloads through the parser alone, the extraction eval reads the recorded per-provider cache, and the retrieval eval serves embeddings from the disk cache. A CI run makes zero model calls; the suites whose private caches are absent skip with a visible notice.

## Judge-faithfulness agreement

The template holds 20 verdicts: 10 pre-labelled `labelled_by: agent` (this ticket's initial labels) and 10 awaiting the owner. The model judge claimed `supported` for every answer it served (20/20 by construction — the gate refuses before serving what it does not support), so the agreement signal that matters is how often the agent agrees with a served answer.

Agent labels vs the model judge on the agent-labelled rows: 7/10 agree = 0.70. The disagreements are answers that assert specifics their citations do not contain (extra numeric conditions beyond the cited span) — exactly the multi-claim case the single-quote judge passes through. Denominators: 10 agent-labelled of 20 rows; 10 rows remain for the owner.

Faithfulness is judged for free text only — the structured truth checks (filter exact-match, extraction truth sets) are labelled data, never model-judged.
