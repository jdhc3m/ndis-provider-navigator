# 05 — Service extraction with structured output

## What we built

The service extractor and its referee. `scripts/extract_services.py` walks each provider's cached pages (home-first, deduplicated, capped at 8), asks the LLM — through one OpenAI-compatible adapter with retry/backoff and a per-run token budget — for `ServiceClaim(service, evidence_quote, confidence)` plus `age_groups`, `telehealth`, `home_visits`, `languages`, and stores a resumable per-provider JSON cache plus public-safe claim tables (no evidence text in the DB). Nothing from the model is trusted: `domain` accepts a claim only if its evidence quote is a verbatim whitespace-collapsed substring of the page text. On 10 hand-labelled sites the LLM scores macro precision 73% / recall 88% against the regex baseline's 56% / 93%.

## Concepts

- **Structured output:** the adapter requests JSON (`response_format: json_object`) and `parse_extraction()` in `src/ndis/domain/extraction.py` is total — it never raises; malformed payloads become machine-readable rejection reasons instead of exceptions.
- **Deterministic grounding:** `quote_supported()` — the quote must appear in the page (whitespace-collapsed, 25–400 chars, real alphanumeric content). The LLM proposes; the domain disposes. Kotlin analogy: validating a deserialized DTO against the source document in plain code, never trusting the serializer.
- **Port:** `LlmClient` Protocol in `src/ndis/application/extraction.py`; unit tests inject fakes, and the real httpx adapter runs only in evals and extraction runs (like swapping an OkHttp interceptor for a fake in tests).
- **Token budget:** `TokenBudget` is pre-checked then debited with reported usage; `BudgetExhausted` stops the run gracefully without caching a half-extracted provider.
- **Baseline comparison:** `comparison_rows()` — one code path shared by the report script and the `pytest -m eval` test, so the table in `reports/extraction.md` and the CI metric cannot drift apart.

## Decisions and why

- **Evidence substring check lives in `domain`, not in the prompt.** Asking the model to "only quote verbatim" is unverifiable; the check is code. Adversarial tests pin zero-width injection, curly-apostrophe swaps, reworded quotes and length bounds.
- **httpx instead of the `openai` package.** httpx was already in the tree; one fewer dependency to justify. The OpenAI-compatible chat-completions shape is a small POST.
- **Cache per provider under `data/private/extraction/`.** Gemini free tier 429s mid-run (the labelled run finished on the `flash-lite` model via `LLM_MODEL` override after `flash` hit its daily quota); a rerun never repeats a completed provider.
- **The Reviewer earned its keep twice:** the first report table inflated metrics to 100%/100% (row unpacking read truth from the prediction column), and the eval metric only existed in a script `pytest -m eval` never ran. Both fixed with one shared comparison path plus a cache-only eval test.
- **Duplicate claims get their own rejection reason** (`claim_duplicate`) instead of inflating "unsupported", after a real duplicate-claim run tripped the store; the store now keeps the highest-confidence row per (page, service).

## How to say it in an interview

"I extract what services an NDIS provider actually offers from their own website text using a structured-output LLM call — JSON with a service, an evidence quote and a confidence. The rule I care about is that the model is never trusted: a claim only survives if its quote is a verbatim substring of the page, enforced by pure domain code with adversarial tests for zero-width characters and reworded quotes. The adapter is one OpenAI-compatible client behind a Protocol with backoff, pacing and a token budget, and unit tests never touch the network. Because we already had a regex baseline from the Maps crawl, I measured the LLM against it on hand-labelled sites: precision went from 56% to 73% while recall stayed comparable — and the independent reviewer caught my eval inflating itself to 100% before it ever became a headline number."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: what does the LLM extract from a provider's website, and what stops it inventing services?
2. What is the regex baseline, and what was the headline comparison?

**Spot it**

3. Which function checks that an evidence quote really appears in the page text, and which layer is it in?
4. Where is the `LlmClient` interface declared, and what do unit tests pass in its place?

**Defend it**

5. Why must the evidence-quote check live in `domain` rather than the prompt or the adapter?
6. What happens when the token budget runs out mid-provider, and why is that provider not cached?
7. Why do the report script and the eval test share `comparison_rows()`?

**Answers:** 1. A list of `ServiceClaim(service, evidence_quote, confidence)` plus age groups, telehealth, home visits and languages, as JSON. Every quote must be a verbatim substring of the page — checked by plain code, so a claim without real evidence is dropped no matter what the model says. 2. The Maps export already had a 36-pattern keyword matcher (`provider_services`). On the same 10 hand-labelled sites the regex scored precision 56% / recall 93%; the LLM scored 73% / 88% — higher precision at similar recall, both measured. 3. `quote_supported()` in `src/ndis/domain/extraction.py` — `domain`, so no framework and testable against adversarial quotes. 4. `LlmClient` is a `Protocol` in `src/ndis/application/extraction.py`; tests inject a fake that returns canned JSON, and the real httpx adapter (`adapters/llm_client.py`) runs only in evals and extraction runs. 5. The prompt is a suggestion the model can ignore and the adapter is infrastructure; a deterministic, framework-free function is the only place the invariant can be enforced and tested against adversarial input. 6. `BudgetExhausted` stops the run before the provider is written, so a resume re-extracts it whole instead of caching partial claims — cache-on-success-only. 7. One code path means the table in the report and the number CI gates on cannot disagree; two implementations would drift on the first refactor.

## Numbers

- 468 unit/integration tests passed (+57 on this ticket), 1 eval skipped as designed; coverage gate ≥ 80% (measured 97.9% on domain+application).
- Headline: LLM macro P 73% / R 88% vs regex baseline P 56% / R 93% (10 agent-labelled sites, ~44 pages read).
- Baseline available for comparison: 3,746 regex hits across 326 places; 415 linked providers; 126 claims + 10 attribute rows stored from the labelled run.
- Zero new dependencies.
