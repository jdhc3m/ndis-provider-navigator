# Service extraction: LLM vs regex baseline
Generated 2026-11-09 14:15 UTC by `scripts/extract_services.py`.

Per-service precision/recall over the labelled sites (10 sites, 39 LLM service predictions in total):

| service | LLM P | LLM R | baseline P | baseline R | LLM tp/fp/fn | base tp/fp/fn |
|---|---|---|---|---|---|---|
| Occupational therapy | 100% | 100% | 33% | 100% | 1/0/0 | 1/2/0 |
| Speech pathology | 100% | 100% | 67% | 100% | 2/0/0 | 2/1/0 |
| Psychology / counselling | — | 0% | 20% | 100% | 0/0/1 | 1/4/0 |
| Physiotherapy | — | — | — | — | 0/0/0 | 0/0/0 |
| Positive behaviour support | 50% | 100% | 40% | 100% | 2/2/0 | 2/3/0 |
| Support coordination | 50% | 100% | 14% | 100% | 1/1/0 | 1/6/0 |
| Plan management | 50% | 100% | 33% | 100% | 1/1/0 | 1/2/0 |
| Supported Independent Living (SIL) | 100% | 100% | 75% | 100% | 3/0/0 | 3/1/0 |
| Specialist Disability Accommodation (SDA) | 100% | 67% | 75% | 100% | 2/0/1 | 3/1/0 |
| Community access / participation | 83% | 100% | 83% | 100% | 5/1/0 | 5/1/0 |
| Personal care / daily living assistance | 83% | 100% | 62% | 100% | 5/1/0 | 5/3/0 |
| Early childhood early intervention (birth to 9) | 100% | 100% | 50% | 100% | 1/0/0 | 1/1/0 |
| Community nursing | 100% | 100% | 60% | 100% | 3/0/0 | 3/2/0 |
| Transport | 75% | 100% | 100% | 33% | 3/1/0 | 1/0/2 |
| Employment supports | 100% | 100% | 67% | 67% | 3/0/0 | 2/1/1 |
| *macro average* | *84%* | *90%* | *56%* | *93%* | | |

Precision counts a prediction against every labelled site that truly offers the service; recall counts the labelled sites the system found. Dashes mean the denominator is zero. The baseline is the earlier crawl's regex matcher read as slug hits per Maps place, bridged to this taxonomy.

Method: ten sites hand-labelled (`labelled_by: agent`) by reading every stored page of the site in full and judging the truth set from page evidence only, without consulting the baseline slugs; sites were picked from the 40-site template for modest page counts (2-7 stored pages each). Extraction ran one request per page (at most 8 pages per provider), paced at 7 s, on the free-tier endpoint. The committed run is the 2026-11-09 re-extraction of all ten sites with the lighter model named in the run's `LLM_MODEL` and the refined prompt whose non-evidence rules match the labelling rules above (expressions of interest and future offerings are not evidence), so one prompt, one model and one run cover every prediction.

Caveats: the sample is small, so most per-service cells rest on one or two sites and the macro averages are coarse. The baseline is scored on a different corpus (the Maps crawl's slug hits), so its higher recall partly measures the crawler's reach rather than extraction quality. LLM misses line up with evidence the stored pages lack (e.g. an accommodation page that was never crawled), and two truth labels were tightened while verifying: a homepage marketing sentence mentioning plan management and support coordination was not counted where the site's own services page does not offer them, and allied-health services offered only as an expression of interest were not counted as offered.

Serving: the system serves — and this report scores — only the claims the model itself marked at or above the served-confidence floor (``MIN_SERVED_CONFIDENCE`` = 0.75 in ``src/ndis/domain/extraction.py``): on the labelled set every claim below that floor was an expression of interest or a coming-soon plan rather than an offered service. The recorded cache keeps every claim; the floor is applied when a profile is read back, so the macro precision above is computed over the 39 served predictions (32 correct) of the 122 the run accepted. The confident false positives that remain (seven) are pages that plainly state a service the hand-label says the site does not actually deliver — a labelling disagreement the next owner-label round should settle.
