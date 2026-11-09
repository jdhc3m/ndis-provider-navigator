# Entity resolution: Maps places → register providers

Generated 2026-11-09 01:20 UTC by `scripts/resolve_entities.py` from `maps_brisbane.db`
(415 places) against the register in `ndis.db`. Both inputs are local and ignored by
Git; this report contains counts only.

## Counts per match status

| match_status | places | share |
| --- | --- | --- |
| registered | 232 | 55.9% |
| unregistered | 125 | 30.1% |
| revoked_or_banned | 6 | 1.4% |
| ambiguous | 7 | 1.7% |
| not_a_provider | 45 | 10.8% |

## Counts per matching strategy

| strategy | places |
| --- | --- |
| domain | 213 |
| name | 31 |
| fuzzy | 1 |
| category | 45 |
| none | 125 |

`domain` = Maps website host equals a register website host (scheme, `www.`, port, path and
query stripped on both sides; shared hosts such as `facebook.com` carry no evidence).
`name` = normalised Maps title, or its head before ` - ` / ` | ` / `:` / `(` decoration, equals
a normalised register business or legal name. `fuzzy` = `rapidfuzz.fuzz.ratio` of the
normalised title head against every normalised name of the Brisbane-area providers, best
score ≥ 92. `category` = the Google primary category is on
the explicit non-provider list, applied only when neither exact rule hit. `none` = no rule hit.

## Strategy × status

| strategy | registered | unregistered | revoked_or_banned | ambiguous | not_a_provider | total |
| --- | --- | --- | --- | --- | --- | --- |
| domain | 202 | 0 | 4 | 7 | 0 | 213 |
| name | 29 | 0 | 2 | 0 | 0 | 31 |
| fuzzy | 1 | 0 | 0 | 0 | 0 | 1 |
| category | 0 | 0 | 0 | 0 | 45 | 45 |
| none | 0 | 125 | 0 | 0 | 0 | 125 |

When one key (a franchise domain such as `justbettercare.com`, or a common name) maps to several
ABNs, candidates at the place's postcode win, then Brisbane-area candidates; if more than one ABN
remains the place is `ambiguous`. A single `Revoked` register match is `revoked_or_banned`.

## Confidence

| confidence | places |
| --- | --- |
| 0.90 – 1.00 | 271 |
| 0.80 – 0.90 | 11 |
| 0.70 – 0.80 | 0 |
| 0.50 – 0.70 | 110 |
| 0.00 – 0.50 | 23 |

Confidence is a fixed score per rule, not a probability: domain 0.95, name 0.90, minus 0.10 when
a tie-break was needed; fuzzy scales linearly from 0.50 at the threshold to
0.70 at a perfect score, so a fuzzy hit is always below any exact hit;
not_a_provider 0.90; unregistered 0.60 with a website (both exact rules had something to check)
or 0.40 without; ambiguous 0.30.

## Fuzzy threshold

`FUZZY_THRESHOLD = 92` on `fuzz.ratio` (plain Indel similarity of the whole
normalised strings). On this export every best score at or above 92 among places the exact
rules did not link is the same business written differently (`Lifest HealthCare` →
`LIFEST HEALTH CARE`, 97.1; before the title-head name rule existed, `Vitium Cura - NDIS …` →
`VITIUM CURA PTY LTD` and `Bloom Healthcare - Brisbane` → `BLOOM HEALTHCARE PTY LTD` scored
100 here), while the first score below it, 91.3, is already a different business
(`Ndis Support Coordination` → `ISupport Coordination`); 90–92 holds only false positives
such as `Global Disability Support Services` → `A1 Disability Support Services`.
`token_set_ratio`, `partial_ratio` and `WRatio` were rejected because they score a register
name that is merely contained in a title (`disability care` in `Brisbane Disability Care`) as
100 or 90, which links unrelated businesses. Fuzzy runs only over the Brisbane-area subset and
only after the category rule, and is never treated as registration evidence.

## Expected vs measured

| outcome | expected (issue #4) | measured | difference |
| --- | --- | --- | --- |
| registered (incl. revoked_or_banned) | 260 | 238 | -22 |
| unregistered + ambiguous | 110 | 132 | +22 |
| not_a_provider | 40 | 45 | +5 |

Issue #4 counted 214/415 places matching on website domain, 190/415 on normalised name and
263/415 on either. This run matches 213 on domain (214 less the
place whose website is a shared `facebook.com` host) and 31 more
on exact normalised name, so the exact rules link
244 places rather than 263,
and fuzzy adds 1. The gap to the issue's figure is the name rule:
this normaliser only lower-cases, maps `&` to `and`, strips punctuation, a leading `the` and
trailing legal suffixes, so a register name that differs by a generic word (`Amma's Care
Services` vs `Amma's Care Support Services`, `Magnetic Health` vs `Magnetic Health Services`,
`Your Voice Support Coordination` vs `Your Voice Pty Ltd`) is not an exact hit, and `fuzz.ratio`
scores those pairs 77–89, below the threshold. The issue's looser comparison counted such pairs;
the 80–92 band here holds 38 unregistered places of which only one (`Access Bridge Disability
Services` → `Access Bridge Disability Support Services`, 89.2) looks like the same business, so
lowering the threshold would buy one true link for dozens of false ones. Four more places are
lost to register data rather than the matcher: `Allsports Physiotherapy`'s register website is
`https://www.allsportsphysio.com.au or www.backinmotion.com.au`, which parses to no host.
The remaining `unregistered` rows are dominated by support coordinators, SDA and respite
operators, plan managers and allied-health clinics with their own domain and name that do not
appear in the register at all; those are what the hand-check sample (`evals/er_sample.yaml`) is
for, and `ambiguous` is kept separate so a shared franchise domain is never silently resolved.
`not_a_provider` sits a little above the expected 40 even though a register hit beats the
category rule (recruitment agencies, software companies and corporate offices that are
registered count as `registered`), because the explicit list also covers the one-off
categories (`Removalist`, `Labor union`, `Internet service provider`, …) in the export.
