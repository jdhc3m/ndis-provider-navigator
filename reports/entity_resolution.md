# Entity resolution: Maps places → register providers

Generated 2026-11-09 01:42 UTC by `scripts/resolve_entities.py` from `maps_brisbane.db`
(415 places) against the register in `ndis.db`. Both inputs are local and ignored by
Git; this report contains counts only.

## Counts per match status

| match_status | places | share |
| --- | --- | --- |
| registered | 223 | 53.7% |
| unregistered | 120 | 28.9% |
| revoked_or_banned | 6 | 1.4% |
| ambiguous | 16 | 3.9% |
| not_a_provider | 50 | 12.0% |

## Counts per matching strategy

| strategy | places |
| --- | --- |
| domain | 213 |
| name | 31 |
| fuzzy | 1 |
| category | 43 |
| government | 7 |
| none | 120 |

`domain` = Maps website host equals a register website host (scheme, `www.`, port, path and
query stripped on both sides; shared hosts such as `facebook.com` or `*.wixsite.com` and bare
suffixes such as `com.au` carry no evidence). `name` = normalised Maps title, or its head before
` - ` / ` | ` / `:` / `(` decoration, equals a normalised register business or legal name.
`fuzzy` = `rapidfuzz.fuzz.ratio` of the normalised title head against every normalised name of
the Brisbane-area providers, best score ≥ 92. `government` = the website is a
`gov.au` host; `category` = the Google primary category is on the explicit non-provider list;
both apply only when neither exact rule hit. `none` = no rule hit.

## Strategy × status

| strategy | registered | unregistered | revoked_or_banned | ambiguous | not_a_provider | total |
| --- | --- | --- | --- | --- | --- | --- |
| domain | 199 | 0 | 4 | 10 | 0 | 213 |
| name | 23 | 0 | 2 | 6 | 0 | 31 |
| fuzzy | 1 | 0 | 0 | 0 | 0 | 1 |
| category | 0 | 0 | 0 | 0 | 43 | 43 |
| government | 0 | 0 | 0 | 0 | 7 | 7 |
| none | 0 | 120 | 0 | 0 | 0 | 120 |

When one key (a franchise domain such as `rightathome.com.au`, or a common name) maps to
several ABNs, only two things may break the tie: a candidate whose normalised name equals the
normalised title or title head (`Right at Home Brisbane South` → the franchisee of that name,
`Care to Change` → `Care to Change Pty Ltd` rather than `Care to Change Disability Services`),
then a candidate whose head office postcode equals the place's postcode. Being the only
Brisbane-area franchisee does not: `Right at Home Brisbane West` and `Right at Home Australia`
stay `ambiguous` with all 7 franchisees
listed as candidates rather than being linked to Brisbane South. 8 places were
linked after such a tie-break (confidence 0.85).
A lone `name` hit whose only provider is neither at the place's postcode nor in the Brisbane
area (`Disability Support Services` → a trade name in SA, `CPE Group` → WA) is `ambiguous` with
that provider listed, not `registered`: a generic name alone is no evidence that the shopfront
is that business. A `domain` hit to an interstate head office keeps full confidence. A single
`Revoked` register match is `revoked_or_banned`.

## Confidence

| confidence | places |
| --- | --- |
| 0.90 – 1.00 | 270 |
| 0.80 – 0.90 | 8 |
| 0.70 – 0.80 | 0 |
| 0.50 – 0.70 | 105 |
| 0.00 – 0.50 | 32 |

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
only after the government and category rules, and is never treated as registration evidence.

## Expected vs measured

| outcome | expected (issue #4) | measured | difference |
| --- | --- | --- | --- |
| registered (incl. revoked_or_banned) | 260 | 229 | -31 |
| unregistered + ambiguous | 110 | 136 | +26 |
| not_a_provider | 40 | 50 | +10 |

Issue #4 counted 214/415 places matching on website domain, 190/415 on normalised name and
263/415 on either. This run finds a domain candidate for 213
places (214 less the one on a shared `facebook.com` host) and a name candidate for
31 more, so the exact rules find something for
244 places rather than 263, and
fuzzy adds 1. Of those, 10 domain and
6 name hits end `ambiguous` rather than `registered`, which is deliberate: the
domain ones are franchises and corporate groups whose shared website maps to several ABNs that
neither the title nor the postcode can separate (and three providers registered twice under the
same name), the name ones are generic names whose only register holder is interstate.
Resolving them the other way would add 16 links at the cost of
the wrong-franchisee links the first version of this matcher made.

The rest of the gap to the issue's figure is the name rule: this normaliser only lower-cases,
maps `&` to `and`, strips punctuation, a leading `the` and trailing legal suffixes, so a register
name that differs by a generic word (`Amma's Care Services` vs `Amma's Care Support Services`,
`Magnetic Health` vs `Magnetic Health Services`, `Your Voice Support Coordination` vs `Your Voice
Pty Ltd`) is not an exact hit, and `fuzz.ratio` scores those pairs 77–89, below the threshold.
The issue's looser comparison counted such pairs; the 80–92 band here holds 37 unregistered
places of which only one (`Access Bridge Disability Services` → `Access Bridge Disability
Support Services`, 89.2) looks like the same business, so lowering the threshold would buy one
true link for dozens of false ones. Four more places are lost to register data rather than the
matcher: `Allsports Physiotherapy`'s register website is `https://www.allsportsphysio.com.au or
www.backinmotion.com.au`, which parses to no host. The remaining `unregistered` rows are
dominated by support coordinators, SDA and respite operators, plan managers and allied-health
clinics with their own domain and name that do not appear in the register at all; those are
what the hand-check sample (`evals/er_sample.yaml`) is for.

`not_a_provider` sits above the expected 40 for two reasons: 7
places are government agencies on `gov.au` hosts (the NDIA's offices, Disability Services
Queensland, the health ombudsman) that the issue's category-only rule would have left
`unregistered`, and the explicit category list also covers the one-off categories
(`Removalist`, `Labor union`, `Internet service provider`, …) in the export. A register hit
still beats both rules, so registered recruitment agencies, software companies and councils
count as `registered`.
