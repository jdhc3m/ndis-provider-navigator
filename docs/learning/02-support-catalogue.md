# 02 — Support catalogue and price limits

## What we built

The deterministic price path. `scripts/fetch_public_data.py --only catalogue` downloads the NDIS Support Catalogue 2026-27 (v1.1, 128 KB XLSX) and keeps it only if it is a real workbook with the expected sheets and more than 900 items dated in the 2026-27 financial year. `domain/catalogue.py` parses rows into frozen `SupportItem` values with `Decimal` prices and exposes `price_limit(item, state, remoteness)`. A `SupportItemRepository` port and use case live in `application/catalogue.py`; the openpyxl reader and the `support_items` table live in `adapters/`, sharing one engine with the register. `GET /price-limits?item=&state=&remoteness=` returns the limit plus all three remoteness tiers and a `source` label. No LLM touches a price.

## Concepts

- **Pure function over a value object:** `price_limit` takes a `SupportItem` and two enums and returns `Decimal | None`. No IO, no database, so the ten hand-checked tests are one-line assertions. Kotlin analogy: a top-level `fun priceLimit(item: SupportItem, ...)` in a pure module. `src/ndis/domain/catalogue.py`.
- **Decimal money:** `Decimal("82.57")`, never `float`. Excel stores `271.59` as the double `271.58999999999997`; `Decimal(str(cell))` uses Python's shortest round-trip repr to recover `271.59`, then a whole-cent check rejects anything like `82.575` instead of rounding. Equivalent to `BigDecimal` with an explicit scale check.
- **Exact-match testing against the source:** each test case cites sheet, row, column and raw cell, and the expected value was typed from the spreadsheet, not from running the code. The Reviewer re-opened the workbook independently and confirmed all ten. `tests/test_domain_catalogue.py`.
- **Provenance:** the URL is discovered from the "What is the support catalogue" page, the server's `Content-Disposition` filename (`v1_1`) is logged as version evidence, and the API response carries `source: "NDIS Support Catalogue 2026-27"`. The workbook itself has no version cell. `README.md` "Prices" section.
- **Spreadsheet ingestion guards:** fetch and loader both reject a 2025-26 workbook (every row outside 2026-07-01..2027-06-30 is `start_date_outside_financial_year`), an HTML error page ("not a zip file") and a truncated file, so a wrong year can never be silently substituted.
- **`StrEnum` for closed sets:** `State` and `Remoteness` are enums; FastAPI turns an unknown value into a 422 at the edge, so the domain never sees `"NZ"`.

## Decisions and why

- **The 2026-27 catalogue has no per-state prices, so `state` returns the national tier.** Both sheets carry only `National`, `Remote`, `Very Remote` columns (earlier years had ACT..WA). The acceptance criterion asked for state distinctions; fabricating them would have been worse than documenting the gap. `state` is still validated and echoed so ticket 07's intent model can pass it through unchanged. Alternative rejected: dropping the parameter and changing the API contract the brief fixed.
- **Prices stored as decimal text in SQLite.** SQLAlchemy `Numeric` on SQLite is float-backed and warns; integer cents would add a conversion in both directions. Text round-trips `Decimal` exactly and is readable in a DB browser.
- **Quotable and unpriced items return 200 with `price_limit: null`.** 56 items are quote-based and 169 more (AT consumables, SIL weekly) have no catalogue price. The item exists, so 404 would be wrong; a null with the `quotable` flag lets ticket 07 explain "quote required" instead of refusing.
- **Item numbers matched exactly, case-sensitive.** The catalogue contains the mixed-case item `Bereavement`, so `.upper()` normalisation would break a real lookup. Documented in README instead.
- **`openpyxl` added as a runtime dependency and to the import-linter forbidden list.** The Reviewer found that the "no external dependencies" contract is a denylist and `openpyxl` was not on it, so a stray `import openpyxl` in `domain/` would have passed. One line in `pyproject.toml` closed it.
- **Current sheet wins over Legacy on duplicate item numbers; legacy items keep a flag and `end_date`.** None overlap in v1.1, but the rule is tested so a future workbook cannot silently change which price wins.

## How to say it in an interview

"Prices in this system are never generated. The support catalogue is a 1,017-row spreadsheet published by the NDIA; a script downloads it with provenance checks — real workbook, expected sheets, more than 900 items dated in the fixed 2026-27 year — so an old catalogue or an HTML error page is deleted, not loaded. Rows become frozen value objects with `Decimal` prices; Excel's doubles like `271.58999999999997` are recovered exactly and anything that is not whole cents is rejected rather than rounded. `price_limit()` is a pure function, tested against ten cells I hand-checked by sheet and row and that an independent reviewer re-checked. The honest finding was that this year's catalogue has no per-state columns, only national, remote and very remote, so the API validates `state` but says plainly that every state returns the national figure — I would rather document a gap than invent a number. The reviewer also caught that my layering contract was a denylist missing `openpyxl`; one line fixed it."

## Check yourself

1. Why `Decimal(str(cell))` rather than `Decimal(cell)` or `round(cell, 2)` for an openpyxl float?
2. A user asks for item `01_003_0107_1_1` in NSW. The response is 200 with `price_limit: null`. Why not 404, and what should the answer layer say?
3. The import-linter contract reported "KEPT" even when `openpyxl` was imported in `domain/`. Why, and what is the general lesson?

## Numbers

- 205 tests (90 new), inner-layer coverage 99.50%, import-linter contracts 2 kept.
- Catalogue v1.1: 128,678 bytes; 976 current + 41 legacy = 1,017 items, 0 rejected; 56 quotable, 169 unpriced; 2,376 price cells, 298 of them stored as 17-digit doubles, 0 sub-cent.
- Ten hand-checked cells across both sheets and all three tiers (e.g. `Current Support Items!N696` = `271.59`, `Legacy Support Items!O38` = `6405.38`).
- 2 review rounds: 1 must-fix and 2 nits in round 1, 0 in round 2.
- CI `quality` job: 21 s on the green run <https://github.com/jdhc3m/ndis-provider-navigator/actions/runs/42528431306>. PR: <https://github.com/jdhc3m/ndis-provider-navigator/pull/21>.

---

Answers: 1. `Decimal(cell)` keeps the full binary expansion (`271.58999999999997…`), and `round` would silently accept and hide a genuinely wrong value like `82.575`; `str()` gives the shortest repr that round-trips, which is the number the spreadsheet author typed, and the whole-cent check turns anything else into a loud rejection. 2. The item exists in the catalogue; it is quote-based (`quotable: true`), so the price is agreed per plan. The answer layer should say "quote required under the catalogue" rather than refuse or invent a figure. 3. The contract is a `forbidden_modules` denylist, so any package not listed is allowed; every time a new third-party dependency is adopted it must be added, or the "no external dependencies" rule is only a convention again.
