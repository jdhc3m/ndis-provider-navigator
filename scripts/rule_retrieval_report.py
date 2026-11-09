"""Write ``reports/retrieval.md``: the measured hit@3 of both chunking strategies over the
ten golden questions, the similarity spread that sets the refusal threshold, and — with
``--answers`` — sample ``AskRule`` runs through the real bounded adapter.

Retrieval itself is cache-only after the first run (embeddings are disk-cached); the
``--answers`` section makes one judge call per sampled question.
"""

import argparse
import logging
import sys
from pathlib import Path

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.golden_questions import golden_pairs, read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.pricing_pdf import PricingPdf
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.adapters.tracing import flush, traced
from ndis.application.extraction import TokenBudget
from ndis.application.pricing import (
    DEFAULT_TOP_K,
    AskRule,
    hit_rate,
    retrieval_rows,
)
from ndis.config import (
    DEFAULT_SIMILARITY_THRESHOLD,
    embed_model,
    llm_api_key,
    llm_base_url,
    llm_model,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data" / "public" / "pricing_arrangements.pdf"
DEFAULT_INDEX = ROOT / "data" / "rule_index.db"
DEFAULT_CACHE = ROOT / "data" / "private" / "embedding_cache.db"
DEFAULT_GOLDEN = ROOT / "evals" / "golden.yaml"
DEFAULT_REPORT = ROOT / "reports" / "retrieval.md"

logger = logging.getLogger("rule_retrieval_report")


def fmt_rate(rate: float | None) -> str:
    return "n/a" if rate is None else f"{100 * rate:.0f}%"


def retrieval_table(rows, k: int = 3) -> list[str]:  # type: ignore[no-untyped-def]
    lines = [
        "| question | expected page | top-3 pages | top-1 similarity | hit@3 |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        pages = ", ".join(str(page) for page in row.retrieved_pages)
        similarity = "n/a" if row.top_similarity is None else f"{row.top_similarity:.3f}"
        lines.append(
            f"| {row.question[:70]} | {row.expected_page} | {pages} | {similarity} "
            f"| {'yes' if row.hit(k) else 'NO'} |"
        )
    return lines


def build_report(  # type: ignore[no-untyped-def]
    pdf_path,
    index_path,
    cache_path,
    golden_path,
    counts: list[tuple[str, int]],
    *,
    with_answers: bool,
    answer_questions: int,
) -> str:
    golden = read_golden(golden_path)
    with PricingPdf(pdf_path) as pdf:
        page_count = pdf.page_count
    store = SqliteVecRuleStore(index_path)
    dimension = store.dimension()
    cache = EmbeddingCache(cache_path)
    client = CachedEmbeddingClient(
        traced(
            OpenAICompatibleClient(
                base_url=llm_base_url(),
                api_key=llm_api_key(),
                model="unused-for-embeddings",
                embed_model=embed_model(),
                budget=TokenBudget(1_000_000),
                interval=20.0,
                backoff_base=20.0,
                backoff_cap=75.0,
            ),
            "rule_embedding",
        ),
        cache,
        embed_model(),
    )
    try:
        pairs = golden_pairs(golden.questions)
        rows = {
            strategy: retrieval_rows(client, store, strategy, pairs, k=3)
            for strategy in ("fixed", "heading")
        }
        off_topic_rows = retrieval_rows(
            client, store, "heading", [(golden.off_topic_question, 0)], k=DEFAULT_TOP_K
        )
    finally:
        cache.close()
        store.close()
        flush()

    lines = [
        "# Retrieval report — pricing rules RAG (ticket 06)",
        "",
        f"Source: NDIS Pricing Arrangements and Price Limits 2025-26 v1.1 "
        f"({page_count} PDF pages). Index: `{index_path.name}` "
        f"({dimension}-dimensional embeddings, one vec0 table per strategy).",
        "",
        "## Corpus",
        "",
        "| strategy | chunks |",
        "|---|---|",
    ]
    for strategy, count in counts:
        lines.append(f"| {strategy} | {count} |")
    lines += [
        "",
        "Chunking: fixed = 600-estimated-token windows packed at paragraph (then sentence) "
        "boundaries inside a page; heading-aware = the same packing, but a page is first cut "
        "at its table-of-contents section boundaries so a rule section never shares a chunk "
        "with the previous section's text. Both strategies are the same parameterised code "
        "path in `src/ndis/domain/pricing.py`.",
        "",
        "## hit@3 over the ten golden questions",
        "",
    ]
    for strategy in ("fixed", "heading"):
        lines += [
            f"### {strategy}",
            "",
            *retrieval_table(rows[strategy]),
            "",
            f"**hit@3: {fmt_rate(hit_rate(rows[strategy], 3))}** "
            f"({sum(1 for row in rows[strategy] if row.hit(3))}/{len(rows[strategy])})",
            "",
        ]

    golden_similarities = sorted(
        row.top_similarity for row in rows["heading"] if row.top_similarity is not None
    )
    off_topic_similarities = sorted(
        row.top_similarity for row in off_topic_rows if row.top_similarity is not None
    )
    lines += [
        "## Refusal threshold",
        "",
        f"- Weakest golden question (heading strategy): top-1 similarity "
        f"**{golden_similarities[0]:.3f}**.",
        f'- Off-topic question ("{golden.off_topic_question}"): top-1 similarity '
        f"**{off_topic_similarities[0]:.3f}**.",
        f"- Chosen threshold: **{DEFAULT_SIMILARITY_THRESHOLD}** (env "
        f"`RULE_SIMILARITY_THRESHOLD`); it separates the off-topic band from the on-topic "
        f"floor, and the judge LLM remains the second gate behind it. Refusal reasons, in "
        f"order: no chunks, below threshold, judge unsupported, no verbatim citation.",
        "",
    ]
    if with_answers:
        lines += sample_answers(index_path, cache_path, golden_path, answer_questions)
    return "\n".join(lines) + "\n"


def sample_answers(  # type: ignore[no-untyped-def]
    index_path, cache_path, golden_path, answer_questions: int
) -> list[str]:
    """Real AskRule runs over the first golden questions plus the off-topic case."""
    golden = read_golden(golden_path)
    store = SqliteVecRuleStore(index_path)
    cache = EmbeddingCache(cache_path)
    embedder = CachedEmbeddingClient(
        traced(
            OpenAICompatibleClient(
                base_url=llm_base_url(),
                api_key=llm_api_key(),
                model=llm_model(),
                embed_model=embed_model(),
                budget=TokenBudget(2_000_000),
            ),
            "rule_embedding",
        ),
        cache,
        embed_model(),
    )
    llm = traced(
        OpenAICompatibleClient(
            base_url=llm_base_url(),
            api_key=llm_api_key(),
            model=llm_model(),
            budget=TokenBudget(2_000_000),
        ),
        "rule_judge",
    )
    use_case = AskRule(
        embedder, store, llm, strategy="heading", threshold=DEFAULT_SIMILARITY_THRESHOLD
    )
    lines = [
        "## Sample answers (real adapter runs, heading strategy)",
        "",
        "Judge model: the configured `LLM_MODEL` at run time; the 2026-11-09 run used a "
        "daily-quota fallback override for the generation model after the primary hit its "
        "free-tier daily limit (the retrieval numbers above do not depend on it).",
        "",
    ]
    try:
        picks = golden.questions[:answer_questions]
        for question in [item.question for item in picks] + [golden.off_topic_question]:
            result = use_case(question)
            if result.refused:
                lines += [
                    f"- **{question}** → refused ({result.reason}), "
                    f"top similarity {result.top_similarity:.3f}"
                ]
            else:
                citations = "; ".join(f'p{page}: "{quote}"' for page, quote in result.citations)
                lines += [
                    f"- **{question}**",
                    f"  - answer: {result.answer}",
                    f"  - citations: {citations}",
                    f"  - top similarity {result.top_similarity:.3f}",
                ]
    finally:
        store.close()
        cache.close()
        llm.close()
        flush()
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--answers", action="store_true", help="include real judge-run samples")
    parser.add_argument("--answer-questions", type=int, default=3)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    store = SqliteVecRuleStore(args.index)
    counts = [(strategy, store.count(strategy)) for strategy in ("fixed", "heading")]
    store.close()
    report = build_report(
        args.pdf,
        args.index,
        args.cache,
        args.golden,
        counts,
        with_answers=args.answers,
        answer_questions=args.answer_questions,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    logger.info("wrote %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
