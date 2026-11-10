# 11 — README, demo script, limitations

## What we built

The finishing ticket: a README that makes the project reproducible and interview-ready, a five-minute demo script (`docs/demo.md`) and a three-minute screen-recording checklist (`docs/recording-checklist.md`). The README carries a Mermaid architecture diagram matching the real layers, data sources with verified licence statements (including PyMuPDF's AGPL, named rather than hidden), setup/run steps that distinguish public-data bootstrap from private-data steps, measured results copied from `reports/`, and an honest "Not built yet" list. The demo script contains only commands that were actually run on the machine with LLM access, and the builder proved a fresh clone in a temp directory could follow it end to end — which also closed the two "not verified live" items left by tickets 09 and 10.

## Concepts

- **Docs as verified artefacts:** the acceptance criterion made documentation testable — a fresh clone (`/tmp`, throwaway DB path) must reach a working demo by following the README literally. A command that has not been run is treated as a bug, the same way an untested function is.
- **Architecture storytelling:** the diagram's point is not the boxes but the one sentence it supports — the model is used in exactly four places (intent parsing, service extraction, rule answering, embeddings; visible in `application/ask.py`, `application/extraction.py`, `application/pricing.py`) and everything else is deterministic, each LLM output passing a check in `domain/`.
- **Honest limitations:** the "Not built yet" list (fuzzy vector name match, model routing, multi-turn, auth/rate limits, PAPL version tracking, full judge validation, plus measured follow-ups like the head-office-centroid distance origin) is sourced from the ticket notes, not invented — each item names where it came from.
- **Demo design around failure:** the script shows the refusal case, the 429/502 recovery path, and the caveat lines on purpose; the showcase question from the original brief parses as a decision request and refuses, so the script answers it as two split questions and keeps the refusal as the honesty moment.
- **Responsible agent workflow:** the builder ran the walkthrough and the fresh-clone loop; a separate reviewer got the issue and diff only (never the builder's summary) and verified claims with its own probes in `/tmp` copies before approving.

## Decisions and why

- **Verify the README by running it, not reading it.** The fresh-clone loop caught real bugs: the register download is 42 MB (the README had inherited a memory-peak figure), the global pnpm differs from the repo pin so `corepack prepare pnpm@11.11.0 --activate` must be documented, and the free-tier 429→502 behaviour needed its recovery path written down. Rejected: trusting the accumulated working checkout, which has data, caches and env a fresh reader won't have.
- **Keep model/vendor names out of the new docs.** The README says "hosted OpenAI-compatible endpoint configured via env"; the demo's fallback-model note points at `docs/harness/STATE.md` instead of naming the model, honouring the AGENTS.md rule that names live only in `config.py` and env.
- **The walkthrough observation beats a generic script.** Demo steps record what was actually observed (20 of 36 providers ranked from 4120, 9 `exact` / 11 `postcode_centroid`; price `$156.16` with its "from support catalogue — not generated" pill), including the model-dependent caveat on the parsed-intent chips.
- **Reports stay untouched.** `reports/` are historical measurement records; the README copies from them and cites them rather than restating numbers in new words.

## How to say it in an interview

"The last ticket was making the whole thing reproducible: the README's setup steps were verified by following them in a fresh clone in a temp directory — public data bootstrap, embedding-index build, web build, live API — and every number in it is copied from a measurement report rather than written from memory. The demo script shows the interesting parts honestly: the model parses the question into a structured intent, the providers come from the Commission register with evidence quotes from their own websites, the price limit comes from the support catalogue not the model, the rule answer carries page citations, and when I ask 'which one is best' it refuses. It also documents what happens when the free tier throttles. The Not-built-yet list names real limitations — no multi-turn, no auth, no model routing — because in an interview the honest list is worth more than a polished one."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: what does ticket 11 deliver, and how was it verified?
2. Why does the demo script deliberately include a refusal?

**Spot it**

3. Where in the README is the list of things that were not built, and where did each item come from?
4. Which three `application/` files contain the project's only LLM call sites?

**Defend it**

5. Why must the demo script contain only commands that were actually run?
6. Why is "Not built yet" a first-class README section sourced from ticket notes?
7. Why verify docs in a fresh `/tmp` clone instead of the working checkout?

**Answers:** 1. The README (architecture diagram, data sources and licences, how to run, eval tables, limitations), a five-minute demo script and a recording checklist. Verified by following the README literally in a fresh `/tmp` clone until the demo worked — an unrun command counts as a bug. 2. Because refusing is a feature, not a failure: the brief's showcase question parses as a decision request and is refused, so the script splits it into two answerable questions and keeps the refusal as the honesty moment the interviewer should see. 3. The "Not built yet" section; every item is traced to a ticket's follow-up note (fuzzy vector name match, model routing, multi-turn, auth/rate limits, PAPL version tracking, full judge validation, head-office-centroid distance origin) rather than invented as boilerplate. 4. `application/ask.py` (intent parse), `application/extraction.py` (service extraction), `application/pricing.py` (rule judge and embeddings). Everything else is deterministic and each LLM output passes a check in `domain/`. 5. An untested command is a live failure during the interview; the script's value is that every step, its duration and its failure mode were observed. 6. Generic disclaimer lists read as boilerplate; each item traceable to a ticket's follow-up note makes the limitations concrete and shows the engineering judgement behind what was deferred. 7. The working checkout has accumulated state (data, caches, env vars) that hides exactly the gaps a new user hits; reproducibility means reaching a working demo from nothing.

## Numbers

- Python: 736 passed / 6 skipped, coverage 95%; `pytest -m eval --no-cov` 8 passed / 1 skipped (~97 s; skip = ER sample awaiting owner verdicts). Gates + `check_no_stubs.py` + `check_private_data.py --tracked` green. CI: `quality` 42 s, `web` 47 s.
- Fresh clone (temp dir, public path): register 42.5 MB ≈ 4 min → 29,574 providers / 64,734 outlets; catalogue 1,017 items; postcodes 3,173 centroids; rule index ≈ 5 min paced (~135k tokens per `reports/cost.md`); `pnpm` build pass; `POST /ask` 1.5 s with the fallback model after reproducing the documented 429→502.
- Live walkthrough (canonical data): `/ask` postcode 4120 → 20 of 36 providers shown, 9 `exact` / 11 `postcode_centroid`; rule answer page 26, similarity 0.76–0.78; two refusals contained; page 502 error state renders with "Try again".
- Review: REQUEST_CHANGES (1 must-fix: recording-checklist shot timings sum to 180 s, not "~170 s"; 2 nits) → fixed → APPROVE, 0 remaining.
- Diff: 3 files, +390/−280. Found report-only (pre-existing): `adapters/ask_evidence.py` never populates `ClaimedService.fetched_at`, so the API's `services[].fetched_at` is always null (cosmetic; recorded as follow-up).
