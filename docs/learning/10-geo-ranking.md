# 10 — Geo ranking

## What we built

Provider results can now be ranked by straight-line distance from a postcode. `GET /providers?near=4101&max_km=5` and the `/ask` provider cards show "3.0 km" on every provider, labelled with where the number came from: `exact` when the provider was matched to a Google Maps listing in ticket 03 (so we know its real coordinates), or `postcode_centroid` when all we know is the head-office postcode from the register. Centroids come from a public Australian postcode CSV loaded into a new `postcode_centroids` table (3,173 postcodes). Unknown postcodes return a readable 422; `/ask` falls back to no distances and says so in its step summary. Nothing is geocoded by an LLM and no paid geospatial service is involved.

## Concepts

- **Haversine distance:** the great-circle distance between two points on a sphere, `src/ndis/domain/geo.py` `haversine_km`, using the IUGG mean radius 6371.0088 km. It is a pure function of four numbers, so its tests compare against values computed another way (spherical law of cosines in a throwaway script) rather than against itself.
- **Centroid vs exact coordinates:** a postcode centroid is the mean of the located rows for that postcode in the public CSV (`postcode_centroid` in `geo.py`); it can be tens of kilometres off in rural postcodes. `Distance.source` carries the distinction all the way to the card so a user never mistakes an approximation for a measurement. Kotlin analogue: a sealed class with two subtypes instead of a bare `Double`.
- **Rank before paginate:** `application/providers.py` fetches the whole matched set's locations, ranks in Python, then slices for `limit`/`offset`. Sorting a page in SQL and then by distance would give a different "nearest" on every page. 29,574 providers rank in ≈8 ms.
- **Deterministic exclusion rules as data decisions:** the CSV contains Australia Post's postcode 9999 ("North Pole", geocoded to Melbourne GPO) typed as an ordinary delivery area. It is excluded by an explicit, commented `NON_GEOGRAPHIC_POSTCODES` list in `domain/geo.py`, counted separately from parse rejections, and documented in the README — the rule is visible, tested and reversible.
- **Port per use case:** the `PostcodeCentroids` Protocol lives beside `ProviderRepository` in `application/providers.py` and is implemented by `SqlAlchemyPostcodeCentroids` in `adapters/sqlite_repository.py`; the domain never sees SQL.

## Decisions and why

- **Haversine on postcode centroids, not a geocoder.** Deterministic, offline, free, and testable to the metre. Rejected: Google/Mapbox geocoding (paid, non-deterministic, external dependency) and LLM-based location inference (unverifiable).
- **Exact coordinates only from `provider_links` rows with `match_status = registered`.** Ambiguous or unregistered Maps matches never contribute coordinates; a provider with several registered rows uses the one nearest to the origin.
- **`max_km` is inclusive and applied to the shown one-decimal value.** The Reviewer found a provider displayed as "5.0 km" that `max_km=5` excluded because the cut-off used the unrounded value; the fix shares one `shown_km` between filter and response so they can never disagree.
- **9999 excluded by list, PO-box postcodes kept.** No CSV column distinguishes 9999 (it is typed "Delivery Area"), so a one-entry list beats a heuristic that would also hit real places like Santa Teresa NT. PO-box and LVR postcodes keep their post-office coordinates because 19 registered providers list one as head office and the post office is the best available origin. Rejected: filtering by the `type` column.
- **No suburb geocoding.** The parsed intent carries a postcode or nothing; adding suburb lookup would add a fuzzy step to a ticket about determinism.

## How to say it in an interview

"Distance is the one ranking signal in the product that's fully deterministic, so I kept it pure: a haversine function in the domain layer with tests whose expected values I derived separately, a postcode centroid table loaded from a public CSV, and a `Distance` value that always carries its source — exact Maps coordinates or an approximate postcode centroid — all the way to the UI label. Ranking runs over the whole matched set before pagination, which is about eight milliseconds for thirty thousand providers. The review caught a real edge case: the filter used the unrounded distance while the response showed one decimal, so a provider displayed at 5.0 km could be excluded by `max_km=5`; now both use the same rounded value. And the data had a surprise — Australia Post's 'North Pole' postcode 9999 geocoded to Melbourne — which I excluded with an explicit, documented list rather than a heuristic."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. In two sentences: how does a provider get a distance, and why are there two kinds?
2. What does the API tell the user when a distance is only approximate?

**Spot it**

3. Which function computes the distance between two coordinates, and which layer is it in?
4. Where is postcode 9999 excluded, and why is it a named constant rather than a filter buried in the loader?

**Defend it**

5. Why must distance ranking happen before `limit`/`offset` rather than on the returned page?
6. Why does the API label every distance with `exact` or `postcode_centroid` instead of just returning a number?
7. Why were the haversine test values computed with a different formula instead of running `haversine_km` once and pasting its output?

**Answers:** 1. Providers matched to a Maps row use its exact latitude/longitude; register-only providers use the centroid of their postcode from a public Australian postcode CSV. Two kinds because the register has no coordinates, only postcodes. 2. `Distance.source` is `exact` or `postcode_centroid` all the way to the card, so an approximation that may be tens of kilometres off in a rural postcode is never shown as a measurement. 3. `haversine_km()` in `src/ndis/domain/geo.py` — pure `domain`, four numbers in, kilometres out, tested against values computed by a different formula. 4. `NON_GEOGRAPHIC_POSTCODES = frozenset({"9999"})` in `src/ndis/domain/geo.py` (Australia Post's "North Pole" geocoded to Melbourne GPO); a named, commented constant is visible, tested, counted separately in the load report and easy to reverse. 5. Pagination is defined over the ranked order; sorting a SQL page (ordered by name) by distance would show the alphabetically-first providers' nearest subset, not the nearest providers overall, and each page would be independently "nearest". 6. A centroid distance can be tens of kilometres wrong in large postcodes; a plain number would present an approximation as a measurement, which contradicts the product's rule that every claim shows its evidence. 7. A test whose expected value comes from the code under test only proves the code is self-consistent; an independent derivation (law of cosines, hand check) would catch a wrong radius, swapped lat/long, or degrees-vs-radians bug.

## Numbers

- Python: 736 tests passed, 6 skipped, coverage 95.0%; `pytest -m eval --no-cov` 6 passed / 3 skipped with the register DB (3 / 6 without). Web: 16 Vitest tests unchanged in count; bundle 248 kB (76 kB gzip).
- Centroid data: 18,559 CSV rows → 6 rejected (`0,0` coordinates) → 1 excluded (9999) → 3,173 postcodes. 4000 centroid −27.4684, 153.0230 (10 localities); 4101 −27.4843, 153.0097 (7).
- Haversine checks: 4000→4350 115.942 km, 4000→4870 1390.696 km, 4000→4101 2.150 km; Reviewer's independent values agree to the metre. Live exact-source check: ABN with three Maps rows → nearest 3.045 km, API 3.0 km.
- Live: `?near=4101&limit=20` 0.08 s end to end (≈8 ms haversine over 29,574 providers); `?near=4101&max_km=5` → 309 providers, first 50 all ≤ 1.6 km, 27 exact / 23 centroid.
- Review: APPROVE, 0 must-fix, 4 nits (2 applied before merge, 2 recorded as follow-ups). CI: `quality` 35 s, `web` 33 s.
