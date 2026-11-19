# 13 — Suburb names in provider questions

## What we built

People ask "providers in South Bank", not "postcode 4101" — so now the system understands suburbs. The postcodes CSV we already download (ticket 10) turned out to hold a gazetteer: 17,984 Delivery-Area rows became a `localities` table (17,959 after dedupe, 16,033 distinct names). The intent schema gains a `locality` field that the model copies verbatim, and a pure function in `domain` resolves it against the gazetteer to one of three honest outcomes: one (state, postcode); `locality_unknown`; or `locality_ambiguous` with the candidate list. Ambiguity became a first-class answer: the web page shows buttons like "Ask again with QLD 4101", so a tie is the user's call, never the model's guess.

## Concepts

- **Gazetteer:** a table mapping place names to (state, postcode), loaded from the same CSV as the centroids in `scripts/load_postcodes.py`; visible in the `localities` table.
- **Name normalisation:** casefold + strip everything non-alphanumeric, so "South Bank", "south bank" and "south-bank" are one key; in `src/ndis/domain/locality.py` `normalise_locality`.
- **Ambiguity as a result, not a guess:** `resolve_locality` returns candidates instead of picking one — the resolver would rather refuse than be wrong, like the refusals of ticket 07.
- **Guarding a model field against the question text:** if the model invents a `postcode` that does not appear in the question (`postcode_in_question` in `domain/intent.py`), the filter set is dropped — the model extracts, the code validates.
- **"Half-understood filters never execute":** an unknown or ambiguous suburb runs no SQL at all, while the price and rule parts of the same question still answer — in `application/ask.py`.

## Decisions and why

- **Resolution in code, not in the model.** The model copies the name; a pure function with a loaded gazetteer does the lookup. Alternative rejected: letting the model output a postcode — unmeasurable, untestable, and it would invent postcodes the CSV never had.
- **No "did you mean" suggestions.** Typo correction is out of scope (issue #50): suggestions need an edit-distance policy and a UX language we have not measured. Ambiguity buttons cover the real cases we verified (South Bank/Southbank, West End ×3).
- **Postcode wins over suburb.** If a question says both and they disagree, the postcode runs and the suburb is reported as a mismatch, never silently dropped — a person who typed a postcode probably knows it better than the gazetteer dedupe.
- **The distance origin stays the postcode centroid.** A suburb's own centroid would be nicer, but the register's postcode linkage is what we can defend; the postcode covers other suburbs and the answer says so.

## How to say it in an interview

"When a user says 'South Bank', the model's job is only to copy that string out of the question — the lookup is a pure function over a 16,000-name gazetteer loaded from the postcodes CSV we already ship. The interesting design choice is that ambiguity is a first-class result: 'West End' exists in three states, so instead of guessing, the resolver returns the three candidates and the UI asks the user to pick — the same refusal-first instinct as the rest of the system. I also guard the model against itself: if it hallucinates a postcode that isn't in the question, the filter set is dropped before any SQL is built."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. What are the three outcomes of resolving a suburb, and what does the system do with each?
2. Why is the gazetteer lookup in code instead of the model?
3. What happens to the price and rule parts of a question whose suburb is ambiguous?

**Spot it**

4. Where is the normalisation that makes "South Bank" and "south-bank" the same key?
5. Which check stops the model from inventing a postcode the user never typed?
6. Which table did `scripts/load_postcodes.py` gain, and where do its rows come from?

**Defend it**

7. A suburb and a postcode appear in one question and disagree. Defend the rule that the postcode wins.

**Answers:** 1. Resolved (run the existing postcode filter, disclose suburb→postcode and that it covers other suburbs), `locality_unknown` (no provider query; the other parts still answer), `locality_ambiguous` (no provider query; the candidates are shown and the web page offers buttons to re-ask with one). 2. A pure function over a loaded gazetteer is testable with expectations read from the CSV, deterministic, and cannot invent a postcode; the model only extracts text it was given. 3. They are still answered — the provider section says why it ran nothing, per "half-understood filters never execute". 4. `normalise_locality` in `src/ndis/domain/locality.py` (casefold + strip non-alphanumerics). 5. `postcode_in_question` in `src/ndis/domain/intent.py`; the filter set is dropped with a machine reason before any SQL. 6. The `localities` table (name, state, postcode), from the Delivery-Area rows of the same public postcodes CSV as the centroids. 7. The register's postcode is the field actually queried and the person typed it deliberately; a suburb name can be shared by several postcodes (the gazetteer proves it), so overriding a deliberate postcode with a fuzzy name would be the guess. The suburb is reported as a mismatch so nothing is silent.

## Numbers

- Gazetteer: 18,559 CSV rows → 17,984 Delivery-Area rows → 17,959 `localities` persisted (24 duplicated source pairs), 16,033 distinct normalised names; centroids unchanged at 3,173.
- Golden: 7 new locality cases — 5 resolve with register totals re-verified against the loaded register (South Bank+QLD 4101 = 44; Whites Hill 4152 = 25; Fortitude Valley therapeutic 4006 = 23; Surfers Paradise approved 4217 = 75; postcode 4000 = 94), 1 ambiguous (Southbank: QLD 4101 / VIC 3006), 1 unknown; plus 1 suburb+recommendation refusal. Filter 12/12, refusals 6/6, locality 7/7.
- intent_cache regenerated under the new prompt: 25 payloads, all zero-tolerance parseable (net restored and extended).
- Checks: 792 passed / 7 skipped locally (was 741/1); coverage ≈95.8%; eval with the loaded register 9 passed / 3 skipped; `pnpm --dir web check` green (18 Vitest tests); web bundle ≈249 kB (76 kB gzip).
- Review: round 1 APPROVE with 1 must-fix (a 422 message that misdescribed a resolved suburb) → fixed `eaa6b0d`; round 2 APPROVE, 0 must-fix. Worked 2026-11-19 (user `jackson`).
