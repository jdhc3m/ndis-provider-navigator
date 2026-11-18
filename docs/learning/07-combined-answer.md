# 07 — Intent parsing and the combined answer

## What we built

The front door. `POST /ask` takes one plain-language question and the LLM's only new job is to parse it into a structured `Intent(provider_filters, price_query, rule_question, decision_request)`. Everything after that is deterministic orchestration: filters run as SQL over the register, price lookups call the pure `price_limit()`, rule questions go to ticket 06's cited-answer pipeline, and provider services come from ticket 05's extraction cache with every quote re-verified against the stored page text. "Which one is best?" and funding questions refuse outright. A pure domain guard checks the composed answer before it is returned: no provider may be named unless it is in the executed result set, no service may be claimed without a verbatim evidence quote, and the professions caveat must appear exactly whenever services are used.

## Concepts

- **Intent schema:** one JSON object with four optional parts; the total parser in `domain/intent.py` drops an invalid part whole rather than half-understanding it (an invalid filter is never partially executed).
- **Deterministic orchestration:** the use case (`application/ask.py`) is a router over ports — the single new `client.complete` call site is the intent parse; everything else is code. Kotlin analogy: a ViewModel that only maps use-case results, never "thinks".
- **Output guard:** `check_answer` in `domain/answer.py` — pure, adversarially tested, and symmetric (missing caveat rejected when services are used; caveat rejected when they are not).
- **Injection resistance:** the prompt declares the question to be data; hostile inputs replay to an empty intent or `decision_request` refusal, and a fake model emitting "I am the admin now… NaN" still yields a 200 refusal with the rule pipeline provably never invoked.
- **Refusal policy:** `decision_request: true` → refusal with the explanation that the system does not make recommendations or funding decisions — and zero other execution.

## Decisions and why

- **Per-part drop-and-reject.** A payload with a bad filter and a good price query executes the price only; nothing half-understood runs. Alternative rejected: per-field salvage, which creates intents the user didn't ask for.
- **Evidence re-verification at answer time.** The extraction cache is trusted only after each quote passes `quote_supported` against the stored page again; missing page files degrade to "no mention", never to an error or an ungrounded claim.
- **The caveat is a byte-exact constant** (`CAVEAT_SERVICES`), pinned by a test against trailing spaces, `;`→`:` swaps and curly apostrophes — a paraphrased caveat is a failed guard, not a fuzzy match.
- **Guard failure echoes the parsed intent but withholds results** — transparency about what was understood, without exposing unverified output.
- **Golden filters pinned to `state: NT` for the Darwin case** — verified identical register results; kept the model's parse rather than "correcting" it, because the AC is exact-match behaviour, not model opinions.

## How to say it in an interview

"The combined answer endpoint is deliberately boring: one structured-output call turns the question into typed intent — filters, price query, rule question, or a refusal flag — and then everything is deterministic code. I never let the model touch execution: filters are SQL, price limits are a pure function over the support catalogue, rule answers come from the citation-checked RAG pipeline, and service claims are re-verified verbatim against the provider's own page text at answer time. The interesting part is the output guard in the domain layer: it rejects answers that name a provider outside the result set, attach a service without a real quote, or forget the exact caveat that the register doesn't record professions. We tested it adversarially — fabricated providers, zero-width characters, prompt injection — and the guard held; even a fake model that answered 'I am the admin now' produced a clean refusal because nothing downstream trusts the model."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: what does `POST /ask` do with a free-text question before any data is touched?
2. Which kinds of question are refused outright, and who decides — the model or the code?

**Spot it**

3. Which function turns the model's JSON into an `Intent`, and what does it do with a postcode like `"41O1"`?
4. Which function is the output guard, and name two of the violations it can report.

**Defend it**

5. Why must the caveat be a byte-exact constant instead of a semantic check?
6. What does "drop the part whole" mean for an intent with an invalid filter and a valid price query?
7. Why is the evidence quote re-verified at answer time even though extraction already validated it?

**Answers:** 1. One LLM call parses it into a typed `Intent` with four optional parts: provider filters, a price query, a rule question, and a `decision_request` flag. Everything after that is deterministic code — SQL, `price_limit()`, or the ticket 06 pipeline. 2. Decision requests ("which is best", "approve my funding"), unparseable model output, and questions that map to nothing (including prompt injections). The model only sets a flag or returns nulls; the refusal itself is three `if` statements in `Ask.__call__` (`application/ask.py`). 3. `parse_intent()` in `src/ndis/domain/intent.py`; an invalid postcode fails `_valid_postcode`, so the whole `provider_filters` part is dropped with a rejection reason and nothing half-understood is executed. 4. `check_answer()` in `src/ndis/domain/answer.py`; any two of `provider_not_in_results`, `provider_name_mismatch`, `evidence_page_missing`, `evidence_quote_not_in_page`, `caveat_missing`, `caveat_unneeded`. 5. A semantic check is an LLM judgement — exactly what the guard exists to constrain; exact matching is deterministic, testable against adversarial variants, and the wording is a product decision. 6. The filter part is dropped with a machine-readable reason and the price query still executes — partial understanding of one part never contaminates the other. 7. The cache is a store, not a source of truth: files can change, be missing, or be stale; re-checking at answer time is what makes the guard independent of how the cache was built.

## Numbers

- Filter accuracy 12/12 exact-match on the golden set (intent parse replayed from cache, SQL executed live); refusal/adversarial cases 5/5 contained with expected reasons.
- 634 unit/integration tests passed (+85), 5 eval tests passing (extraction, rule retrieval ×2, ask golden ×2), 1 designed skip; 95.3% coverage on domain+application.
- Zero new dependencies; one new LLM call site (intent parsing).
