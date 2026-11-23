# 14 — Service names instead of support item numbers

## What we built

People ask "what's the hourly price limit for occupational therapy?", not "for 15_617_0118_1_3" — so now the system understands the service name. A pure search in `domain` maps a phrase to support items: deterministic normalisation plus a small, test-pinned alias table ("OT" → "Occupational Therapist", "physio", "speech therapy", "psychology"…), because the catalogue writes full titles that no substring match would hit. `GET /support-items?q=&limit=` exposes the same search with no model and no key. In `/ask`, the model's only new job is copying the phrase out of the question (`price_query.service`); everything after that is code: match, order (base items before `_PT`-style claim-type variants, current before legacy, then item number), cap the list, state the total, and re-check every price through the deterministic `price_limit()` before anything is shown.

## Concepts

- **Lexical search with an alias table:** normalise, split into tokens, match an ordered contiguous token run against item names, with a fixed alias table for words the catalogue does not use; in `src/ndis/domain/service_search.py`.
- **One name, one price:** measured on the 2026-27 catalogue — 139 item names are shared by several item numbers and none of them carries two different national limits, which is what makes listing candidates safe; visible in `tests/test_service_search_catalogue.py`.
- **Guard on a model field against the question text:** an `item` the model invents that is absent from the question is dropped (`item_not_in_question` in `src/ndis/domain/intent.py`) and never looked up — the model extracts, the code validates (same instinct as ticket 13's `postcode_not_in_question`).
- **Candidates, not a choice:** the answer lists matching items capped at 12 with the total stated; which variant applies (`_CA`, `_PT`, …) is the participant's and provider's call, not the system's; in `application/ask.py` `price_section_stage` and web `ServicePriceCard.tsx`.
- **A new eval floor for a new capability:** `service_resolution_accuracy` in `evals/thresholds.yaml` — the fraction of service cases whose expected item numbers all appear in the items shown.

## Decisions and why

- **Code lookup, not the model.** The model copies the phrase; a pure function does the lookup. Alternative rejected: embedding/vector search over item names — an index and threshold to tune for a 780-name vocabulary a deterministic matcher already covers, and one more place the model could silently be wrong. (Ticket 12 showed we can compare engines when we want to.)
- **Alias table over fuzzy matching.** "OT" → "Occupational Therapist" is a fixed, test-pinned mapping. Alternative rejected: stemming or edit-distance matching — plausible surprises we cannot measure, versus a table we can enumerate.
- **No state now answers, instead of refusing.** `price_query_no_state` no longer drops the query: the 2026-27 catalogue has no per-state prices (ticket 02), so the national tier is the full truth and the answer says so. Alternative rejected: keeping the refusal — it refused a question the catalogue fully answers.
- **The model never supplies a code or a price.** An item number is honoured only when it appears in the question text; every shown price is recomputed in `domain/answer.py` (`price_limit_mismatch`), so a hallucinated price has nowhere to render.

## How to say it in an interview

"A user asks for the price limit for occupational therapy. The model's only job is to copy that phrase out of the question — the lookup is deterministic code: normalise, apply a small alias table for abbreviations like OT, match token runs against the catalogue, and order base items before claim-type variants, current before legacy. The answer lists every matching item with its price from the support catalogue, capped with the total stated — we deliberately do not pick 'the' item for the user. And the output guard recomputes every displayed price through the same deterministic function the catalogue populates, so even if a model tried to state a price, it could not render."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. Why can the system safely list every item that shares a name instead of picking one?
2. What does the model contribute to a service price question, and what does it never contribute?
3. What changed for a price question that mentions no state?

**Spot it**

4. Where does "OT" become "Occupational Therapist", and what pins that mapping?
5. Which guard stops the model from looking up an item number it invented?
6. Which endpoint lets you check the service→items mapping without an API key or a model?

**Defend it**

7. Defend "alias table now, embeddings later if ever" against the objection that a synonym list will always be incomplete.

**Answers:** 1. The measurement showed 139 current item names are shared by several item numbers and none carries two different national limits — so one name means one price, and listing is safe; the variant that applies is the participant's/provider's call. 2. It copies the phrase verbatim into `price_query.service`; it never supplies an item code or a price — codes are honoured only when present in the question text and prices are recomputed by `price_limit()`. 3. `price_query_no_state` no longer drops the query: the national tier is used and the answer says so, because the 2026-27 catalogue has no per-state prices to invent. 4. The alias table in `src/ndis/domain/service_search.py`, pinned by unit tests in `tests/test_domain_service_search.py`. 5. `item_not_in_question` in `src/ndis/domain/intent.py` — the price query is dropped with a machine reason and never looked up. 6. `GET /support-items?q=&limit=` — pure repository + domain code, no model, no key. 7. The vocabulary is small (780 distinct names), bounded and slow-changing, so missing aliases surface as `unknown_service` — an honest, testable failure we can extend case by case; embeddings would trade that for silent near-misses (wrong item, plausible name) plus an index and threshold to maintain, which only pays off if alias growth becomes unmanageable — measurable as a rising `unknown_service` rate on real questions.

## Numbers

- Catalogue: 1,017 items, 780 distinct names; 139 shared names with no conflicting national limits; 335 claim-type variants (letter suffixes), 41 legacy.
- `GET /support-items?q=occupational%20therapist`: total 30, the five base OT items first, each $193.99 national (expected values read from the XLSX in tests; the Reviewer re-verified against the XLSX independently with openpyxl).
- New floor `service_resolution_accuracy: 0.85`; measured 8/8 = 1.0. Filter 12/12, refusals 6/6 unchanged; the four existing floors byte-identical.
- Checks: 865 passed / 14 skipped (was 792/7), coverage 95.36%; eval 11 passed / 3 skipped; `pnpm --dir web check` green (22 Vitest tests); `intent_cache.yaml` regenerated: 35 payloads under the new prompt.
- The captures were re-taken via the `LLM_MODEL` env fallback because the configured model's daily quota was exhausted — no model name committed, all captures verified through the real parser (same precedent as ticket 13); two golden questions were reworded during capture and their expected item numbers re-verified against the XLSX.
- Screenshots `service-*` / `unknown-service-*` at 1440/375 px; `docs/demo.md` re-run live as written (2026-11-23).
- Review: APPROVE round 1, 0 must-fix; 5 non-blocking nits recorded in the PR and as follow-ups in `docs/harness/STATE.md`. Worked 2026-11-23 (user `jackson`).
