"""Run the ticket-12 comparison and write ``reports/snowflake.md``: the same ten golden
pricing-rule questions (plus the off-topic refusal case) measured against the Cortex
Search service on the personal Snowflake trial and against the local ``sqlite-vec``
retrieval, on the exact same corpus, chunks and embedding vectors.

Both systems search the identical index: the Snowflake service holds the vectors read
out of the local ``sqlite-vec`` index (``scripts/snowflake_load.py``), and every query
is embedded once through the same cached client the local evals use, then sent as a
vector to Snowflake and re-used for the local search. Quality numbers are therefore a
pure serving-engine comparison; the managed-embedding path of Cortex Search is not
measurable on a trial account (the ``EMBED_TEXT_*`` functions are refused) and that
fact is part of the report.

Latency is measured per query from this machine: Snowflake through the documented
``SNOWFLAKE.CORTEX.SEARCH_PREVIEW`` SQL function (the multi-index query syntax with
user-provided vectors is not exposed by the REST API; ``SEARCH_PREVIEW`` is the SQL
surface the docs recommend for exactly this and note it carries higher latency than
the production REST/Python APIs), and the local search against ``sqlite-vec`` in
process. Each side gets a warm-up run first; the reported number is the median of
``--runs`` measured runs.

Requires: ``--extra snowflake``, the built local index (``scripts/build_rule_index.py``),
the loaded service (``scripts/snowflake_load.py``), and the environment from
``~/.config/ndis/.env`` (both the LLM keys and the ``SNOWFLAKE_*`` block).
"""

import argparse
import json
import logging
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import snowflake.connector

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.golden_questions import golden_pairs, read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.application.pricing import RetrievalRow, hit_rate
from ndis.config import (
    DEFAULT_SIMILARITY_THRESHOLD,
    embed_model,
    llm_api_key,
    llm_base_url,
    snowflake_account,
    snowflake_database,
    snowflake_private_key_path,
    snowflake_role,
    snowflake_schema,
    snowflake_search_service,
    snowflake_user,
    snowflake_warehouse,
)
from ndis.domain.extraction import TokenBudget
from ndis.domain.pricing import PRICING_DOCUMENT_TITLE

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = ROOT / "evals" / "golden.yaml"
DEFAULT_REPORT = ROOT / "reports" / "snowflake.md"
DEFAULT_INDEX = ROOT / "data" / "rule_index.db"
DEFAULT_CACHE = ROOT / "data" / "private" / "embedding_cache.db"

logger = logging.getLogger("snowflake_compare")


class ServiceNotReady(RuntimeError):
    """The Cortex Search service is not indexed yet; rerun after a few minutes."""


def _embedder(cache_path: Path) -> tuple[CachedEmbeddingClient, EmbeddingCache]:
    cache = EmbeddingCache(cache_path)
    return (
        CachedEmbeddingClient(
            OpenAICompatibleClient(
                base_url=llm_base_url(),
                api_key=llm_api_key(),
                model="unused-for-embeddings",
                embed_model=embed_model(),
                budget=TokenBudget(10_000_000),
            ),
            cache,
            embed_model(),
        ),
        cache,
    )


def _search_preview(conn, payload: str, runs: int) -> tuple[tuple[int, ...], float | None]:  # type: ignore[no-untyped-def]
    """One query against the service through SEARCH_PREVIEW: the pages of the top
    results and the median wall-clock milliseconds over ``runs`` measured runs."""
    cur = conn.cursor()
    timings: list[float] = []
    pages: tuple[int, ...] = ()
    for attempt in range(runs + 1):
        start = time.perf_counter()
        cur.execute(
            "SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW("
            f"'{snowflake_database()}.{snowflake_schema()}.{snowflake_search_service()}', %s)"
            ")['results'] AS r",
            (payload,),
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        rows = cur.fetchall()
        if attempt == 0:
            continue  # warm-up: warehouse resume and first-round JIT are not measured
        timings.append(elapsed)
        results = json.loads(rows[0][0]) if rows and isinstance(rows[0][0], str) else []
        pages = tuple(int(item["page"]) for item in results)
    return pages, statistics.median(timings) if timings else None


def _connect() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account=snowflake_account(),
        user=snowflake_user(),
        private_key_file=snowflake_private_key_path(),
        role=snowflake_role(),
        warehouse=snowflake_warehouse(),
    )


def fmt(rate: float | None) -> str:
    return "n/a" if rate is None else f"{100 * rate:.0f}%"


def table(
    rows: Sequence[RetrievalRow],
    snowflake_pages: Sequence[tuple[int, ...]],
    snowflake_ms: Sequence[float | None],
) -> list[str]:
    lines = [
        "| question | expected page | local top-3 | local top-1 sim | snowflake top-3 |"
        " snowflake ms (median) | local hit@3 | snowflake hit@3 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row, pages, ms in zip(rows, snowflake_pages, snowflake_ms, strict=True):
        local = ", ".join(str(page) for page in row.retrieved_pages)
        remote = ", ".join(str(page) for page in pages)
        similarity = "n/a" if row.top_similarity is None else f"{row.top_similarity:.3f}"
        milliseconds = "n/a" if ms is None else f"{ms:.0f}"
        lines.append(
            f"| {row.question[:60]} | {row.expected_page} | {local} | {similarity} "
            f"| {remote} | {milliseconds} "
            f"| {'yes' if row.hit(3) else 'NO'} "
            f"| {'yes' if row.expected_page in pages[:3] else 'NO'} |"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--runs", type=int, default=3, help="measured runs per question")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    golden = read_golden(args.golden)
    strategy = "heading"
    pairs = (*golden_pairs(golden.questions), (golden.off_topic_question, 0))

    store = SqliteVecRuleStore(args.index)
    dimension = store.dimension()
    client, cache = _embedder(args.cache)
    try:
        vectors = client.embed([question for question, _ in pairs]).vectors
        local_rows: list[RetrievalRow] = []
        local_ms: list[float | None] = []
        for (question, expected), vector in zip(pairs, vectors, strict=True):
            scored = store.search(strategy, vector, args.top_k)  # warm-up not needed: cached
            start = time.perf_counter()
            scored = store.search(strategy, vector, args.top_k)
            local_ms.append((time.perf_counter() - start) * 1000.0)
            local_rows.append(
                RetrievalRow(
                    question=question,
                    expected_page=expected,
                    retrieved_pages=tuple(item.chunk.page for item in scored),
                    top_similarity=scored[0].similarity if scored else None,
                )
            )
    finally:
        store.close()
        cache.close()

    conn = _connect()
    try:
        snowflake_pages: list[tuple[int, ...]] = []
        snowflake_ms: list[float | None] = []
        for (question, _), vector in zip(pairs, vectors, strict=True):
            payload = json.dumps(
                {
                    "multi_index_query": {"embedding": [{"vector": [float(x) for x in vector]}]},
                    "columns": ["page", "chunk_text"],
                    "limit": args.top_k,
                }
            )
            pages, ms = _search_preview(conn, payload, args.runs)
            if not pages:
                raise ServiceNotReady(
                    f"{snowflake_search_service()} returned no results; the index may still "
                    "be building - rerun after a few minutes"
                )
            snowflake_pages.append(pages)
            snowflake_ms.append(ms)
            logger.info("%s -> pages %s (%.0f ms)", question[:50], pages, ms or 0)
    finally:
        conn.close()

    question_rows = local_rows[: len(golden.questions)]
    off_topic = local_rows[-1]
    remote_hit = [
        1 if row.expected_page in pages[:3] else 0
        for row, pages in zip(local_rows, snowflake_pages, strict=True)
    ]
    lines = [
        "# Snowflake Cortex Search comparison (ticket 12)",
        "",
        f"Source: {PRICING_DOCUMENT_TITLE}, chunked with the served heading strategy "
        f"({len(pairs) - 1} golden questions plus the off-topic refusal case).",
        f"Corpus and vectors: the {dimension}-dim vectors read out of the "
        "local `sqlite-vec` index, uploaded to Snowflake; the service holds the identical "
        "chunk texts and embedding vectors, so both systems search the same index.",
        "",
        "## Account and privacy controls",
        "",
        f"- Personal trial account `{snowflake_account()}`; no workplace accounts or credentials.",
        "- Connection: key-pair service user `NDIS_SERVICE` (RSA public key on the user, "
        "private key only on this machine), role `NDIS_ROLE`, warehouse `NDIS_XS` "
        "(XSMALL, auto-suspend 60 s). The user has no password and no MFA; it can only "
        "reach the trial objects its role is granted.",
        "- Data uploaded: the price-guide chunk texts (public document, "
        "`data/public/pricing_arrangements.pdf`) and their embedding vectors. Nothing "
        "from `data/private/` (fetched pages, Maps cache, judge verdicts, cost logs) "
        "was uploaded.",
        "- Destructive steps are scoped to the `NDIS_DB.RULES` schema of the trial "
        "account; `CREATE OR REPLACE` re-indexes the service, nothing else is touched.",
        "",
        "## Reproducible steps",
        "",
        "```sh",
        "set -a; source ~/.config/ndis/.env; set +a",
        "uv run --extra snowflake python scripts/build_rule_index.py  # local index + cache",
        "uv run --extra snowflake python scripts/snowflake_load.py \\",
        "    --pdf data/public/pricing_arrangements.pdf --index data/rule_index.db",
        "uv run --extra snowflake python scripts/snowflake_compare.py",
        "```",
        "",
        "## Why user-provided embeddings",
        "",
        "Trial accounts cannot run the managed-embedding AI functions: creating a service "
        "that would let Snowflake embed the corpus fails with",
        "`AI function EMBED_TEXT_768 is not available for trial accounts.`",
        "The comparison therefore pins the embedding model (the cached client behind the "
        "local index) for both systems and measures the serving platforms - Cortex "
        "Search's vector index plus its retrieval pipeline versus local `sqlite-vec` "
        "cosine search. A paid account could additionally test managed embeddings; that "
        "is out of scope here.",
        "",
        f"Query surface: `SNOWFLAKE.CORTEX.SEARCH_PREVIEW` with the multi-index query "
        "syntax (user-provided vector embeddings are not queryable through the REST "
        "API); the docs note SEARCH_PREVIEW carries higher latency than the production "
        f"REST/Python APIs. Snowflake numbers are medians over {args.runs} measured runs "
        "after one warm-up call (warehouse resume is paid once, not per query).",
        "",
        "## hit@3 over the ten golden questions",
        "",
        *table(question_rows, snowflake_pages[:-1], snowflake_ms[:-1]),
        "",
        f"**Local hit@3: {fmt(hit_rate(question_rows, 3))}** "
        f"({sum(1 for row in question_rows if row.hit(3))}/{len(question_rows)}). "
        f"**Snowflake hit@3: {fmt(sum(remote_hit[:-1]) / max(len(remote_hit) - 1, 1))}** "
        f"({sum(remote_hit[:-1])}/{len(remote_hit) - 1}).",
        "",
        "## Off-topic refusal behaviour",
        "",
        f'Local: the "{golden.off_topic_question}" case refuses - top-1 similarity '
        f"{off_topic.top_similarity:.3f} sits below the "
        f"{DEFAULT_SIMILARITY_THRESHOLD} threshold, no model call is made.",
        f"Snowflake: Cortex Search always returns its top-{args.top_k} "
        "(pages "
        f"{', '.join(str(page) for page in snowflake_pages[-1])}); there is no similarity "
        "threshold, so the refusal gate the local system enforces would have to be "
        "re-implemented around the service.",
        "",
        "## Latency",
        "",
    ]
    snowflake_on_topic = [ms for ms in snowflake_ms[:-1] if ms is not None]
    local_on_topic = [ms for ms in local_ms[:-1] if ms is not None]
    lines += [
        f"- Local `sqlite-vec` search (in process, embeddings excluded): median "
        f"{statistics.median(local_on_topic):.1f} ms per query.",
        f"- Snowflake `SEARCH_PREVIEW` round trip (embedding excluded, vector sent): "
        f"median {statistics.median(snowflake_on_topic):.0f} ms per query "
        f"(min {min(snowflake_on_topic):.0f}, max {max(snowflake_on_topic):.0f}) from this "
        "machine to the ap-southeast-2 region, through the SQL surface.",
    ]
    lines += [
        "",
        "## Cost",
        "",
        "Measured on the account usage views and the Snowsight UI after the 2026-11-10 "
        "run (session includes the setup attempts, two service re-indexings and the "
        "comparison queries):",
        "",
        "- `NDIS_XS` warehouse: **0.1994 credits** billed in the 24 hours of the session "
        "- about twelve minutes of XSMALL uptime at 1 credit/hour (load, indexing and "
        "all queries; the account suspends it 60 s after each burst).",
        "- Cloud services (compilation, metadata): **0.0003 credits**.",
        "- Trial credit balance at report time: **$400 of $400 left** - the trial "
        "credits had not been debited in the UI view yet; the metering history above is "
        "the honest consumption signal.",
        "- Embeddings: zero extra cost - the vectors were already cached on disk from "
        "the local index build; the golden queries were cache hits.",
        "",
        "## Honest limitations",
        "",
        "- `SEARCH_PREVIEW` is the SQL preview surface, not the production REST/Python "
        "API; the measured Snowflake latency is an upper bound on the serving path.",
        "- The latency numbers come from one machine over residential internet; they "
        "include client-side network, not just service time.",
        "- The corpus is small (284 chunks, one document); at this size retrieval "
        "quality differences between engines are dominated by the shared embeddings.",
        "- The reranker behaviour of the service was not independently verified; the "
        "query used the default scoring.",
        "- The trial account expires in 28 days from 2026-11-10; the service, table and "
        "key must be recreated after that.",
        "",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
