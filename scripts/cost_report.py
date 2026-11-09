"""Render ``reports/cost.md``: measured cost and latency per endpoint over the golden
set, plus the judge-agreement numbers from the hand-label template.

Inputs: the private JSONL recordings under ``data/private/cost/`` (one line per real
call: site, endpoint, model, token usage, seconds), the extraction cache's recorded
per-provider token totals, the rule index's chunk texts (for the index-build estimate),
and ``evals/judge_verdicts.yaml`` for the agreement section. Prices come from the
published per-model table in ``src/ndis/config.py``; model ids themselves are never
printed — every endpoint is named by the pipeline step that makes the call.

Latency statistics come only from the dedicated endpoint-measurement runs
(``endpoints-*.jsonl``), where the recorded seconds are the client's own successful-POST
latency; the generation runs (``judge-generation-*.jsonl``) contribute their tokens and
cost, but their wall-clock includes the adapter's pacing wait. Every number is labelled
measured (recorded from real runs) or estimated (derived from recorded sizes at a
measured per-call basis). The report is regenerated with
``uv run python -m scripts.cost_report``; it reads only private data and writes only the
committed markdown.
"""

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from ndis.adapters.golden_questions import read_golden
from ndis.config import token_price
from scripts.build_judge_verdicts import existing_rows
from scripts.recording import COST_DIR, ROOT

GOLDEN = ROOT / "evals" / "golden.yaml"
INDEX = ROOT / "data" / "rule_index.db"
TEMPLATE = ROOT / "evals" / "judge_verdicts.yaml"
REPORT = ROOT / "reports" / "cost.md"
EXTRACTION_DIR = ROOT / "data" / "private" / "extraction"

# The pending full extraction run of ticket 05 covers the crawled providers; the cache
# records what the ten labelled sites cost, and the projection scales by provider count.
PENDING_EXTRACTION_PROVIDERS = 300

# The served answers the verdict-template generation run produced (20 rows).
TEMPLATE_ROWS = 20

SITE_LABELS = {
    "intent_parse": "Intent parse (POST /ask)",
    "intent_capture": "Intent capture (eval recording)",
    "rule_judge": "Rule judge + answer (POST /ask-rule)",
    "rule_embedding": "Question embeddings (retrieval gate)",
    "index_embedding": "Corpus embeddings (index build)",
    "extraction": "Service extraction (per page)",
}

# Deterministic token estimate used everywhere in this report: the same chars/4 heuristic
# the adapter itself falls back to when an endpoint reports no usage.
CHARS_PER_TOKEN = 4


def main() -> int:
    all_rows = read_rows(COST_DIR, "*")
    latency_rows = read_rows(COST_DIR, "endpoints-*")
    if not all_rows:
        raise SystemExit(f"no recordings under {COST_DIR}; run the measure scripts first")
    per_site: dict[str, list[dict[str, object]]] = by_site(all_rows)
    latency_site: dict[str, list[dict[str, object]]] = by_site(latency_rows)
    cache_tokens = extraction_cache_tokens()
    corpus_chars = index_corpus_chars()
    verdicts = existing_rows(TEMPLATE)
    REPORT.write_text(
        render(per_site, latency_site, cache_tokens, corpus_chars, verdicts), encoding="utf-8"
    )
    print(f"wrote {REPORT} from {len(all_rows)} recorded calls")
    return 0


def read_rows(directory: Path, pattern: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(directory.glob(f"{pattern}.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def by_site(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    per_site: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        per_site[str(row["site"])].append(row)
    return per_site


def extraction_cache_tokens() -> tuple[int, int]:
    """(input, output) tokens recorded across the private extraction cache, summed."""
    inputs = outputs = 0
    for path in sorted(EXTRACTION_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        inputs += int(document.get("input_tokens", 0))
        outputs += int(document.get("output_tokens", 0))
    return inputs, outputs


def index_corpus_chars() -> int:
    """The chunk texts of the built rule index, for the index-build cost estimate."""
    connection = sqlite3.connect(INDEX)
    try:
        total = 0
        for (text,) in connection.execute("select text from rule_chunks"):
            total += len(str(text))
        return total
    finally:
        connection.close()


def call_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD for one call at the published table, or None when the model is unpriced."""
    price = token_price(model)
    if price is None:
        return None
    input_price, output_price = price
    return input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price


def site_cost(site_rows: list[dict[str, object]]) -> tuple[float, bool]:
    """(USD total, priced) over one site's recorded calls."""
    total = 0.0
    priced = True
    for row in site_rows:
        cost = call_cost(str(row["model"]), int(row["input_tokens"]), int(row["output_tokens"]))
        if cost is None:
            priced = False
        else:
            total += cost
    return total, priced


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of the recorded seconds."""
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def dollars(value: float) -> str:
    return f"${value:.4f}"


def render(
    per_site: dict[str, list[dict[str, object]]],
    latency_site: dict[str, list[dict[str, object]]],
    cache_tokens: tuple[int, int],
    corpus_chars: int,
    verdicts: list[dict[str, object]],
) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Cost, latency and judge agreement\n",
        f"Generated {when} by `scripts/cost_report.py` from the private run recordings "
        "(`data/private/cost/`, not committed). Prices are the published paid-tier list "
        "prices per 1M tokens in `src/ndis/config.py` (checked 2026-11-09); the runs "
        "themselves went through the free tier, so the dollars are what this usage "
        "would cost at list prices, not what was paid. Model ids stay in `config.py` "
        "and the environment — endpoints are named by the pipeline step that calls.\n",
        "\n## Measured per-call latency and tokens\n",
        "\nReal calls recorded on 2026-11-09 with the lighter fallback model. Seconds "
        "are the client's own successful-POST latency — pacing waits and retry backoffs "
        "excluded — nearest-rank percentile over the dedicated endpoint-measurement "
        "runs; the n is small (3-4 calls per step), so treat p95 as indicative.\n",
        "\n| pipeline step | n | input tokens (mean) | output tokens (mean) | p50 s | "
        "p95 s |\n|---|---|---|---|---|---|\n",
    ]
    for site in ("intent_parse", "rule_judge", "rule_embedding", "extraction"):
        rows = latency_site.get(site, [])
        if not rows:
            continue
        inputs = [int(row["input_tokens"]) for row in rows]
        outputs = [int(row["output_tokens"]) for row in rows]
        seconds = [float(row["seconds"]) for row in rows]
        lines.append(
            f"| {SITE_LABELS[site]} | {len(rows)} | {sum(inputs) // len(inputs)} "
            f"| {sum(outputs) // len(outputs)} | {percentile(seconds, 0.5):.1f} "
            f"| {percentile(seconds, 0.95):.1f} |\n"
        )

    lines.append("\n## Cost over the golden set\n")
    intent_rows = per_site.get("intent_parse", [])
    judge_rows = per_site.get("rule_judge", [])
    golden = read_golden(GOLDEN)
    golden_calls = len(golden.filter_cases) + len(golden.refusal_cases)
    intent_mean = (
        (
            sum(int(row["input_tokens"]) for row in intent_rows) / len(intent_rows),
            sum(int(row["output_tokens"]) for row in intent_rows) / len(intent_rows),
        )
        if intent_rows
        else (0.0, 0.0)
    )
    intent_cost = call_cost(
        str(intent_rows[0]["model"]) if intent_rows else "",
        round(intent_mean[0] * golden_calls),
        round(intent_mean[1] * golden_calls),
    )
    judge_cost, _ = site_cost(judge_rows)
    extraction_in, extraction_out = cache_tokens
    per_provider_mean = (extraction_in + extraction_out) / 10 if extraction_in else 0.0
    lines.append("\n| endpoint | calls | basis | cost USD |\n|---|---|---|---|\n")
    if intent_cost is not None:
        lines.append(
            f"| intent parse over the {golden_calls} golden questions | {golden_calls} "
            f"| estimated: {golden_calls} × the measured per-call mean "
            f"({intent_mean[0]:.0f} in / {intent_mean[1]:.0f} out) | "
            f"{dollars(intent_cost)} |\n"
        )
    if judge_rows:
        lines.append(
            f"| rule judge: the verdict-template generation run plus the latency "
            f"re-measure ({TEMPLATE_ROWS} of the calls served the template's answers; "
            "the rest were refused candidates, which are real calls too) | "
            f"{len(judge_rows)} | measured | {dollars(judge_cost)} |\n"
        )
    if cache_tokens != (0, 0):
        lines.append(
            f"| service extraction, ten labelled sites (the recorded run) | — | "
            f"measured from the cache: {extraction_in} in / {extraction_out} out "
            "tokens | unpriced — see note |\n"
        )
        lines.append(
            f"| pending full extraction run (~{PENDING_EXTRACTION_PROVIDERS} providers) "
            f"| — | estimated: per-provider mean ({per_provider_mean / 1000:.1f}k "
            "tokens) × provider count | unpriced — see note |\n"
        )

    lines.append(
        "\nNote on the extraction run's price: the private cache predates model "
        "provenance — it records the token totals but not which model produced them — "
        "so no dollar figure is asserted for it. Token totals are the measured part; "
        "the missing piece is one provenance field in the cache format, not a "
        "measurement.\n"
    )

    corpus_tokens = corpus_chars // CHARS_PER_TOKEN
    embedding_rows = [
        row for site in ("rule_embedding", "index_embedding") for row in per_site.get(site, [])
    ]
    if embedding_rows:
        embed_cost, _ = site_cost(embedding_rows)
        lines.append(
            f"\nEmbeddings: the recorded embedding calls cost {dollars(embed_cost)} "
            f"measured ({len(embedding_rows)} batch calls; cache hits replay for "
            "nothing). The index build over the whole priced-rules corpus is estimated "
            f"at {corpus_tokens} tokens from {corpus_chars} recorded chunk characters, "
            "the same deterministic estimate the adapter falls back to.\n"
        )

    lines.append(
        "\n## What the eval gate itself costs\n\n"
        "Nothing at run time. `pytest -m eval --no-cov` replays committed captures: the "
        "/ask golden eval replays `evals/intent_cache.yaml`, the intent-accuracy eval "
        "replays the same payloads through the parser alone, the extraction eval reads "
        "the recorded per-provider cache, and the retrieval eval serves embeddings from "
        "the disk cache. A CI run makes zero model calls; the suites whose private "
        "caches are absent skip with a visible notice.\n"
    )

    lines.append("\n## Judge-faithfulness agreement\n\n")
    labelled = [row for row in verdicts if row.get("verdict")]
    agent_rows = [row for row in labelled if row.get("labelled_by") == "agent"]
    agreed = [
        row
        for row in agent_rows
        if (row["verdict"] == "faithful") == bool(row.get("model_supported"))
    ]
    lines.append(
        f"The template holds {len(verdicts)} verdicts: {len(agent_rows)} pre-labelled "
        f"`labelled_by: agent` (this ticket's initial labels) and "
        f"{len(verdicts) - len(agent_rows)} awaiting the owner. The model judge claimed "
        "`supported` for every answer it served "
        f"({sum(1 for row in verdicts if row.get('model_supported'))}/{len(verdicts)} by "
        "construction — the gate refuses before serving what it does not support), so "
        "the agreement signal that matters is how often the agent agrees with a served "
        "answer.\n"
    )
    if agent_rows:
        lines.append(
            f"\nAgent labels vs the model judge on the agent-labelled rows: "
            f"{len(agreed)}/{len(agent_rows)} agree = {len(agreed) / len(agent_rows):.2f}. "
            "The disagreements are answers that assert specifics their citations do not "
            "contain (extra numeric conditions beyond the cited span) — exactly the "
            "multi-claim case the single-quote judge passes through. Denominators: "
            f"{len(agent_rows)} agent-labelled of {len(verdicts)} rows; "
            f"{len(verdicts) - len(agent_rows)} rows remain for the owner.\n"
        )
    lines.append(
        "\nFaithfulness is judged for free text only — the structured truth checks "
        "(filter exact-match, extraction truth sets) are labelled data, never "
        "model-judged.\n"
    )
    return "".join(lines)


if __name__ == "__main__":
    import sys

    sys.exit(main())
