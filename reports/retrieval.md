# Retrieval report — pricing rules RAG (ticket 06)

Source: NDIS Pricing Arrangements and Price Limits 2025-26 v1.1 (104 PDF pages). Index: `rule_index.db` (3072-dimensional embeddings, one vec0 table per strategy).

## Corpus

| strategy | chunks |
|---|---|
| fixed | 175 |
| heading | 284 |

Chunking: fixed = 600-estimated-token windows packed at paragraph (then sentence) boundaries inside a page; heading-aware = the same packing, but a page is first cut at its table-of-contents section boundaries so a rule section never shares a chunk with the previous section's text. Both strategies are the same parameterised code path in `src/ndis/domain/pricing.py`.

## hit@3 over the ten golden questions

### fixed

| question | expected page | top-3 pages | top-1 similarity | hit@3 |
|---|---|---|---|---|
| Can a provider claim 100% of the agreed fee for a short notice cancell | 26 | 26, 26, 27 | 0.750 | yes |
| How many clear business days notice does a participant need to give to | 26 | 26, 26, 27 | 0.811 | yes |
| Can a provider charge for the time spent travelling to a participant,  | 22 | 21, 22, 24 | 0.780 | yes |
| Can telehealth be used to deliver supports and claimed, and under what | 20 | 20, 32, 20 | 0.820 | yes |
| Can a provider claim for time spent on non-face-to-face activities suc | 21 | 21, 20, 28 | 0.765 | yes |
| Are registered providers allowed to add extra charges such as credit c | 40 | 40, 39, 38 | 0.710 | yes |
| Are NDIS supports GST-free? | 40 | 40, 4, 42 | 0.802 | yes |
| How much higher are price limits in remote and very remote areas? | 31 | 31, 23, 22 | 0.747 | yes |
| Do the price limits apply to plan-managed participants? | 10 | 10, 9, 16 | 0.747 | yes |
| Can a provider claim the costs of running and maintaining a facility t | 34 | 34, 69, 52 | 0.723 | yes |

**hit@3: 100%** (10/10)

### heading

| question | expected page | top-3 pages | top-1 similarity | hit@3 |
|---|---|---|---|---|
| Can a provider claim 100% of the agreed fee for a short notice cancell | 26 | 26, 26, 27 | 0.784 | yes |
| How many clear business days notice does a participant need to give to | 26 | 26, 26, 27 | 0.811 | yes |
| Can a provider charge for the time spent travelling to a participant,  | 22 | 21, 22, 28 | 0.780 | yes |
| Can telehealth be used to deliver supports and claimed, and under what | 20 | 20, 32, 20 | 0.843 | yes |
| Can a provider claim for time spent on non-face-to-face activities suc | 21 | 21, 20, 37 | 0.765 | yes |
| Are registered providers allowed to add extra charges such as credit c | 40 | 40, 9, 38 | 0.771 | yes |
| Are NDIS supports GST-free? | 40 | 40, 4, 14 | 0.877 | yes |
| How much higher are price limits in remote and very remote areas? | 31 | 30, 31, 9 | 0.811 | yes |
| Do the price limits apply to plan-managed participants? | 10 | 9, 10, 10 | 0.753 | yes |
| Can a provider claim the costs of running and maintaining a facility t | 34 | 34, 17, 11 | 0.751 | yes |

**hit@3: 100%** (10/10)

## Refusal threshold

- Weakest golden question (heading strategy): top-1 similarity **0.751**.
- Off-topic question ("What is the capital of France?"): top-1 similarity **0.620**.
- Chosen threshold: **0.65** (env `RULE_SIMILARITY_THRESHOLD`); it separates the off-topic band from the on-topic floor, and the judge LLM remains the second gate behind it. Refusal reasons, in order: no chunks, below threshold, judge unsupported, no verbatim citation.

