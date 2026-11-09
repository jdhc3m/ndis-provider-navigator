"""Link the private Maps export to the register, write ``provider_links``, the hand-check
sample and the report.

Usage: ``uv run python scripts/resolve_entities.py [--maps PATH] [--db PATH] [--sample PATH]
[--report PATH]``. Load the register first with ``scripts/load_register.py``. The Maps export
is read only; verdicts already filled in the sample are kept for rows with the same key.
"""

import argparse
import logging
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ndis.adapters.er_sample import merge_verdicts, read_sample, sample_row, write_sample
from ndis.adapters.fuzzy import RapidfuzzScorer
from ndis.adapters.maps_sqlite import MapsExportError, SqliteMapsPlaceSource
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderLinkRepository,
    SqlAlchemyProviderRepository,
    create_sqlite_engine,
)
from ndis.application.entity_resolution import ResolutionResult, ResolveEntities
from ndis.domain.entity_resolution import (
    CONFIDENCE_DOMAIN,
    CONFIDENCE_FUZZY_MAX,
    CONFIDENCE_FUZZY_MIN,
    FUZZY_THRESHOLD,
    TIE_BREAK_PENALTY,
    MatchStatus,
    MatchStrategy,
    ProviderLink,
)
from ndis.domain.evaluation import stratified_sample

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPS = ROOT / "data" / "private" / "maps_brisbane.db"
DEFAULT_DB = ROOT / "data" / "ndis.db"
DEFAULT_SAMPLE = ROOT / "evals" / "er_sample.yaml"
DEFAULT_REPORT = ROOT / "reports" / "entity_resolution.md"
SAMPLE_SIZE = 50
SAMPLE_SEED = 20261109

# Expected outcome from issue #4; the report compares the measured counts with these.
EXPECTED = (
    ("registered (incl. revoked_or_banned)", 260),
    ("unregistered + ambiguous", 110),
    ("not_a_provider", 40),
)
CONFIDENCE_BINS = ((0.9, 1.01), (0.8, 0.9), (0.7, 0.8), (0.5, 0.7), (0.0, 0.5))

logger = logging.getLogger("resolve_entities")


def _table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def _crosstab(links: tuple[ProviderLink, ...]) -> str:
    counts = Counter((link.match.strategy, link.match.match_status) for link in links)
    headers = ("strategy", *[status.value for status in MatchStatus], "total")
    rows: list[tuple[object, ...]] = []
    for strategy in MatchStrategy:
        cells = [counts[(strategy, status)] for status in MatchStatus]
        rows.append((strategy.value, *cells, sum(cells)))
    return _table(headers, rows)


def _confidence_distribution(links: tuple[ProviderLink, ...]) -> str:
    rows: list[tuple[object, ...]] = []
    for low, high in CONFIDENCE_BINS:
        count = sum(1 for link in links if low <= link.match.confidence < high)
        label = f"{low:.2f} – {min(high, 1.0):.2f}"
        rows.append((label, count))
    return _table(("confidence", "places"), rows)


def _candidate_count(links: tuple[ProviderLink, ...], domain: str) -> int:
    return max(
        (len(link.match.candidates) for link in links if link.place.website_domain == domain),
        default=0,
    )


def render_report(result: ResolutionResult, maps: Path, db: Path, when: datetime) -> str:
    """Markdown report: counts per status and strategy, confidence bins, expected vs actual."""
    total = len(result.links)
    by_status, by_strategy = result.by_status, result.by_strategy
    linked = by_status[MatchStatus.REGISTERED] + by_status[MatchStatus.REVOKED_OR_BANNED]
    open_rows = by_status[MatchStatus.UNREGISTERED] + by_status[MatchStatus.AMBIGUOUS]
    actual = (linked, open_rows, by_status[MatchStatus.NOT_A_PROVIDER])
    expected_rows: list[tuple[object, ...]] = [
        (label, expected, measured, f"{measured - expected:+d}")
        for (label, expected), measured in zip(EXPECTED, actual, strict=True)
    ]
    crosstab = Counter((link.match.strategy, link.match.match_status) for link in result.links)
    ambiguous_domain = crosstab[(MatchStrategy.DOMAIN, MatchStatus.AMBIGUOUS)]
    ambiguous_name = crosstab[(MatchStrategy.NAME, MatchStatus.AMBIGUOUS)]
    tie_broken = sum(
        1
        for link in result.links
        if link.match.abn is not None
        and link.match.confidence == round(CONFIDENCE_DOMAIN - TIE_BREAK_PENALTY, 3)
    )
    status_rows: list[tuple[object, ...]] = [
        (status.value, by_status[status], f"{100 * by_status[status] / total:.1f}%")
        for status in MatchStatus
    ]
    strategy_rows: list[tuple[object, ...]] = [
        (strategy.value, by_strategy[strategy]) for strategy in MatchStrategy
    ]
    return f"""# Entity resolution: Maps places → register providers

Generated {when:%Y-%m-%d %H:%M} UTC by `scripts/resolve_entities.py` from `{maps.name}`
({total} places) against the register in `{db.name}`. Both inputs are local and ignored by
Git; this report contains counts only.

## Counts per match status

{_table(("match_status", "places", "share"), status_rows)}

## Counts per matching strategy

{_table(("strategy", "places"), strategy_rows)}

`domain` = Maps website host equals a register website host (scheme, `www.`, port, path and
query stripped on both sides; shared hosts such as `facebook.com` or `*.wixsite.com` and bare
suffixes such as `com.au` carry no evidence). `name` = normalised Maps title, or its head before
` - ` / ` | ` / `:` / `(` decoration, equals a normalised register business or legal name.
`fuzzy` = `rapidfuzz.fuzz.ratio` of the normalised title head against every normalised name of
the Brisbane-area providers, best score ≥ {FUZZY_THRESHOLD:g}. `government` = the website is a
`gov.au` host; `category` = the Google primary category is on the explicit non-provider list;
both apply only when neither exact rule hit. `none` = no rule hit.

## Strategy × status

{_crosstab(result.links)}

When one key (a franchise domain such as `rightathome.com.au`, or a common name) maps to
several ABNs, only two things may break the tie: a candidate whose normalised name equals the
normalised title or title head (`Right at Home Brisbane South` → the franchisee of that name,
`Care to Change` → `Care to Change Pty Ltd` rather than `Care to Change Disability Services`),
then a candidate whose head office postcode equals the place's postcode. Being the only
Brisbane-area franchisee does not: `Right at Home Brisbane West` and `Right at Home Australia`
stay `ambiguous` with all {_candidate_count(result.links, "rightathome.com.au")} franchisees
listed as candidates rather than being linked to Brisbane South. {tie_broken} places were
linked after such a tie-break (confidence {CONFIDENCE_DOMAIN - TIE_BREAK_PENALTY:.2f}).
A lone `name` hit whose only provider is neither at the place's postcode nor in the Brisbane
area (`Disability Support Services` → a trade name in SA, `CPE Group` → WA) is `ambiguous` with
that provider listed, not `registered`: a generic name alone is no evidence that the shopfront
is that business. A `domain` hit to an interstate head office keeps full confidence. A single
`Revoked` register match is `revoked_or_banned`.

## Confidence

{_confidence_distribution(result.links)}

Confidence is a fixed score per rule, not a probability: domain 0.95, name 0.90, minus 0.10 when
a tie-break was needed; fuzzy scales linearly from {CONFIDENCE_FUZZY_MIN:.2f} at the threshold to
{CONFIDENCE_FUZZY_MAX:.2f} at a perfect score, so a fuzzy hit is always below any exact hit;
not_a_provider 0.90; unregistered 0.60 with a website (both exact rules had something to check)
or 0.40 without; ambiguous 0.30.

## Fuzzy threshold

`FUZZY_THRESHOLD = {FUZZY_THRESHOLD:g}` on `fuzz.ratio` (plain Indel similarity of the whole
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

{_table(("outcome", "expected (issue #4)", "measured", "difference"), expected_rows)}

Issue #4 counted 214/415 places matching on website domain, 190/415 on normalised name and
263/415 on either. This run finds a domain candidate for {by_strategy[MatchStrategy.DOMAIN]}
places (214 less the one on a shared `facebook.com` host) and a name candidate for
{by_strategy[MatchStrategy.NAME]} more, so the exact rules find something for
{by_strategy[MatchStrategy.DOMAIN] + by_strategy[MatchStrategy.NAME]} places rather than 263, and
fuzzy adds {by_strategy[MatchStrategy.FUZZY]}. Of those, {ambiguous_domain} domain and
{ambiguous_name} name hits end `ambiguous` rather than `registered`, which is deliberate: the
domain ones are franchises and corporate groups whose shared website maps to several ABNs that
neither the title nor the postcode can separate (and three providers registered twice under the
same name), the name ones are generic names whose only register holder is interstate.
Resolving them the other way would add {ambiguous_domain + ambiguous_name} links at the cost of
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

`not_a_provider` sits above the expected 40 for two reasons: {by_strategy[MatchStrategy.GOVERNMENT]}
places are government agencies on `gov.au` hosts (the NDIA's offices, Disability Services
Queensland, the health ombudsman) that the issue's category-only rule would have left
`unregistered`, and the explicit category list also covers the one-off categories
(`Removalist`, `Labor union`, `Internet service provider`, …) in the export. A register hit
still beats both rules, so registered recruitment agencies, software companies and councils
count as `registered`.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maps", type=Path, default=DEFAULT_MAPS, help="private Maps export")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database to write")
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE, help="hand-check YAML")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="Markdown report")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.maps.is_file():
        logger.error("Maps export not found at %s; nothing to resolve", args.maps)
        return 1
    if not args.db.is_file():
        logger.error("register database not found at %s; run scripts/load_register.py", args.db)
        return 1
    engine = create_sqlite_engine(args.db)
    use_case = ResolveEntities(
        SqliteMapsPlaceSource(args.maps),
        SqlAlchemyProviderRepository(engine),
        SqlAlchemyProviderLinkRepository(engine),
        RapidfuzzScorer(),
    )
    try:
        result = use_case()
    except MapsExportError as exc:
        logger.error("entity resolution failed: %s", exc)
        return 1
    logger.info("resolved %s", result.summary())

    sample = stratified_sample(result.links, SAMPLE_SIZE, SAMPLE_SEED)
    rows = tuple(sample_row(link) for link in sample)
    previous = read_sample(args.sample) if args.sample.is_file() else ()
    rows = merge_verdicts(rows, previous)
    write_sample(args.sample, rows, SAMPLE_SEED)
    kept = sum(row.verdict is not None for row in rows)
    logger.info("wrote %d sample rows to %s (%d verdicts kept)", len(rows), args.sample, kept)

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        render_report(result, args.maps, args.db, datetime.now(UTC)), encoding="utf-8"
    )
    logger.info("wrote report to %s", args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
