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

## Sample answers (real adapter runs, heading strategy)

- **Can a provider claim 100% of the agreed fee for a short notice cancellation?**
  - answer: When a provider experiences a short notice cancellation or no show, they can claim up to 100% of the agreed fee associated with the activity from the participant's plan, provided that specific conditions are met and the claim complies with the NDIS Pricing Arrangements and Price Limits and the terms of the service agreement.
  - citations: p26: "Where a provider has a Short Notice Cancellation (or no show), they can claim up to 100% of the agreed fee associated with the activity from the participant’s plan, subject to the NDIS Pricing Arrangements and Price Limits and the terms of the service agreement with the participant."
  - top similarity 0.784
- **How many clear business days notice does a participant need to give to avoid a short notice cancellation charge for non-DSW supports?**
  - answer: For non-DSW supports, a participant needs to give at least two clear business days' notice of cancellation to avoid a short notice cancellation charge.
  - citations: p26: "Short Notice Cancellation – 2 clear business days • This policy typically applies to non-DSW supports where a participant has provided less than two (2) clear business days’ notice of cancellation for a support"
  - top similarity 0.811
- **Can a provider charge for the time spent travelling to a participant, and how is it limited?**
  - answer: Providers can charge for travel time to a participant if allowed by the pricing rules for that support, the primary support is delivered face-to-face, the participant agrees in advance, and the provider is required to pay the worker for travel time (or is a sole trader). Travel claims are limited by hourly rates (such as 50% of the regular limit for therapy supports) and time caps based on the Modified Monash Model location (typically up to 30 minutes in MMM1-3 areas and up to 60 minutes in MMM4-5 areas for each eligible worker).
  - citations: p21: "Provider Travel Providers can only claim travel costs from a participant’s plan when delivering a support item if all the following conditions are met: • The NDIS Pricing Arrangements and Price Limits indicates that providers can claim for Provider Travel for that support item; and"; p22: "For therapy supports providers, including early childhood therapy, the price limit for provider travel time is 50% of the regular price limit for these items."; p22: "When a provider claims for labour costs (travel time) related to a support, they can claim for up to 30 minutes of travel to and from each participant in MMM1-3 areas and up to 60 minutes in MMM4- 5 areas for each eligible worker. This is known as a travel time limit."
  - top similarity 0.780
- **What is the capital of France?** → refused (below_similarity_threshold), top similarity 0.620
