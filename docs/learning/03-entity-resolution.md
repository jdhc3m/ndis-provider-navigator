# 03 — Private data bootstrap and entity resolution

## What we built

A matcher that links each of the 415 Google Maps places in the private export to a provider in the NDIS Commission register, or says honestly that it cannot. The Maps data has no ABN, so `domain/entity_resolution.py` tries website domain first, then normalised name, then a `rapidfuzz` score, and labels everything else `unregistered`, `ambiguous`, `not_a_provider` (non-provider Google category or a `gov.au` host) or `revoked_or_banned`. `scripts/resolve_entities.py` writes the results to a `provider_links` table (with the Maps coordinates for ticket 10), a 50-row public-safe hand-check file `evals/er_sample.yaml` for Jackson to fill, and `reports/entity_resolution.md` with the measured counts. The private database never leaves `data/private/`, and a test proves the pre-commit guard blocks it.

## Concepts

- **Entity resolution:** deciding that two records from different sources describe the same real-world business. Here `MapsPlace` (title, website, suburb, postcode) versus `Provider` (business/legal name, website domain, head office). `resolve_place()` in `src/ndis/domain/entity_resolution.py`.
- **Blocking key / match key:** a cheap, normalised value that two records must share before they are compared. Website domain is the strongest key (one host, one business), normalised name the second. `website_domain()` in `domain/register.py`, `normalise_name()` and `title_head()` in `domain/entity_resolution.py`.
- **Normalisation:** lower-case, `&` → `and`, strip punctuation, drop a leading "the" and trailing `pty ltd`/`inc`/`limited`, collapse whitespace, so `L'Arche Brisbane Inc.` and `LARCHE BRISBANE` compare equal. Same job as a Kotlin `toKey()` extension before a `HashMap` lookup.
- **Fuzzy threshold:** `rapidfuzz.fuzz.ratio` returns 0–100; `FUZZY_THRESHOLD = 92` was set by looking at the data, not guessed: every score ≥ 92 was the same business spelt differently, the first score below (91.3) was already a different business. The scorer is injected through the `FuzzyScorer` Protocol so `domain/` never imports `rapidfuzz`. `src/ndis/application/entity_resolution.py`, `src/ndis/adapters/fuzzy.py`.
- **Precision denominator:** precision = correct ÷ (correct + incorrect) over labelled rows only; `unsure` rows are excluded and zero labels returns `None`, not 0 or 1. `precision_from_verdicts()` in `src/ndis/domain/evaluation.py`; the eval test skips visibly with "0 of 50 verdicts filled".
- **Stratified deterministic sample:** 50 rows drawn with a fixed seed from every `(match_status, strategy)` stratum, so the rarest rule (one fuzzy hit) is always in the hand-check. `stratified_sample()` in `domain/evaluation.py`.

## Decisions and why

- **Only two things may break a tie: exact name agreement, then postcode.** The first version also accepted "the only Brisbane-area candidate", which linked `Right at Home Brisbane West` and the national franchisor to the Brisbane South franchisee's ABN. The Reviewer caught it by querying the register. Now a shared franchise domain stays `ambiguous` with all candidates listed. Alternative rejected: keep the Brisbane fallback at lower confidence — a wrong ABN is wrong at any confidence.
- **A lone interstate name hit is `ambiguous`, not `registered`.** `Disability Support Services` in Brisbane CBD matched exactly one register name, a trade name in South Australia. A generic name is no evidence the shopfront is that business; a domain hit to an interstate head office keeps 0.95 because a domain is specific.
- **`gov.au` hosts and non-provider Google categories are `not_a_provider`, but a register hit always wins.** The NDIA's own offices and Disability Services Queensland sit in the export; a registered council stays `registered`. One known miss: DSQ's register entry is under the department's new domain, so that row is labelled `not_a_provider` rather than linked (noted in the issue).
- **Fuzzy runs last, only over the 2,403 Brisbane-area providers, and is never registration evidence.** It found exactly one extra link. Lowering the threshold to 80 would buy one true link (`Access Bridge`, 89.2) for dozens of false ones. `token_set_ratio`/`partial_ratio` were rejected because a name merely contained in a title scores 100.
- **The sample key is `sha256(place_id)[:12]`, not the Google place id.** Verdicts survive regeneration, and the committed file carries only Maps title + register name/ABN + status.
- **A tiny in-house YAML writer/reader instead of adding `pyyaml`.** The file is a flat list of scalars; the reader rejects anything it does not understand (`SampleFormatError`) rather than guessing. Alternative rejected: a new dependency for one flat file.
- **Measured 229 linked / 136 unregistered+ambiguous / 50 not_a_provider against the expected 260 / 110 / 40.** The gap is explained row by row in the report (16 deliberate `ambiguous`, 7 government bodies, a stricter name rule, four `Allsports` rows lost to a malformed register website). We chose to report the honest number rather than tune the matcher until it hit the estimate.

## How to say it in an interview

"The scraped Maps data had no ABN, so I built a deterministic entity-resolution pipeline: website domain first, because one host almost always means one business; normalised legal name second; a string-similarity score third, only over the Brisbane subset and at a threshold I set by looking at the score distribution, not by guessing. Every rule emits a status and a fixed confidence, and when a franchise domain maps to seven ABNs the answer is `ambiguous` with the candidates listed, not a guess. The independent review caught exactly that bug in my first version — two Right at Home franchisees linked to the wrong ABN — and the fix was to remove a tie-break, not add one. The result is 223 registered, 120 unregistered, 16 ambiguous and 50 non-providers out of 415, below the rough estimate, and the report explains every row of the gap. Precision is measured on a 50-row stratified hand-check that is honest about having zero labels until a human fills it; the eval skips visibly instead of passing. And the private source database is guarded by a pre-commit check with a test that proves staging it fails."

## Check yourself

1. Why is website domain a better first match key than business name, and when does it fail?
2. Two Approved register rows share `rightathome.com.au`; the Maps title is `Right at Home Australia`, postcode 4064, and neither row is at 4064. What status does the matcher emit and why?
3. Jackson fills 20 verdicts: 15 `correct`, 3 `incorrect`, 2 `unsure`. What precision does the eval report?

## Numbers

- 308 tests (103 new), inner-layer coverage 99.33%, import-linter contracts 2 kept; `rapidfuzz` added to the denylist.
- 415 Maps places → registered 223, unregistered 120, revoked_or_banned 6, ambiguous 16, not_a_provider 50. By strategy: domain 213, name 31, fuzzy 1, government 7, category 43, none 120. 8 tie-broken links at 0.85.
- Fuzzy threshold 92 on `fuzz.ratio`; the 80–92 band holds 37 unregistered rows with one plausible true match.
- Hand-check: 50 rows, 0 of 50 verdicts filled, precision `None` (eval skips visibly).
- 2 review rounds: round 1 had 2 real must-fix defects (wrong-franchisee link, interstate name hit) plus the lockfile process item and 11 nice-to-haves; round 2 had 0 must-fix.
- CI `quality` job: 24 s on the green run <https://github.com/jdhc3m/ndis-provider-navigator/actions/runs/42547555073>. PR: <https://github.com/jdhc3m/ndis-provider-navigator/pull/23>.

---

Answers: 1. A domain is chosen by the business and is unique per host, so an exact host match is strong evidence; names are shared (`Disability Support Services`) and spelt inconsistently. It fails on shared hosts (`facebook.com`, `wixsite.com`, bare `com.au`, which are excluded as evidence) and on franchises where one domain serves many ABNs (handled by the tie-break or `ambiguous`). 2. `ambiguous`, confidence 0.30, with both ABNs as candidates: neither candidate's normalised name equals the title, neither postcode matches, and being Brisbane-area is deliberately not a tie-breaker. 3. 15 ÷ (15 + 3) = 0.833; the 2 `unsure` rows are excluded from the denominator, and the eval prints "18 of 50 verdicts filled (2 unsure); precision 0.833 (15/18)". The provisional ≥ 0.9 bar would fail, which is the point of the gate.
