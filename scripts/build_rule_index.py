"""Build the pricing-rule vector index: chunk the PDF with both strategies, embed through
the bounded adapter (disk cache; reruns never re-embed), store in sqlite-vec.

Smoke first: ``--smoke`` embeds only a few chunks per strategy so retrieval can be
verified before the full corpus is paid for. Requires the environment from
``~/.config/ndis/.env`` (``LLM_BASE_URL``, ``LLM_API_KEY``, ``EMBED_MODEL``).
"""

import argparse
import logging
import sys
from pathlib import Path

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.pricing_pdf import PricingPdf
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.application.pricing import BuildRuleIndex
from ndis.config import embed_model, llm_api_key, llm_base_url
from ndis.domain.extraction import TokenBudget
from ndis.domain.pricing import CHUNK_STRATEGIES, chunk_corpus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data" / "public" / "pricing_arrangements.pdf"
DEFAULT_INDEX = ROOT / "data" / "rule_index.db"
DEFAULT_CACHE = ROOT / "data" / "private" / "embedding_cache.db"

logger = logging.getLogger("build_rule_index")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--strategy", choices=(*CHUNK_STRATEGIES, "both"), default="both")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--smoke",
        type=int,
        default=0,
        metavar="N",
        help="embed only the first N chunks per strategy (smoke test)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not embed_model():
        logger.error("EMBED_MODEL is not set; source ~/.config/ndis/.env first")
        return 1

    with PricingPdf(args.pdf) as pdf:
        pages = pdf.pages()
        headings = pdf.headings()
    logger.info("PDF: %d pages, %d headings", len(pages), len(headings))

    cache = EmbeddingCache(args.cache)
    client = CachedEmbeddingClient(
        OpenAICompatibleClient(
            base_url=llm_base_url(),
            api_key=llm_api_key(),
            model="unused-for-embeddings",
            embed_model=embed_model(),
            budget=TokenBudget(10_000_000),
            # Free tier is limited per minute AND per day; the adapter's own 6 s pacing
            # and 30 s backoff cap are tuned for chat, embeddings need a minute-scale
            # rhythm so a throttled window is waited out rather than abandoned.
            interval=20.0,
            backoff_base=20.0,
            backoff_cap=75.0,
            max_attempts=6,
        ),
        cache,
        embed_model(),
    )
    store = SqliteVecRuleStore(args.index)
    strategies = list(CHUNK_STRATEGIES) if args.strategy == "both" else [args.strategy]
    try:
        for strategy in strategies:
            chunks = chunk_corpus(pages, headings, strategy)
            if args.smoke:
                chunks = chunks[: args.smoke]
            counts = BuildRuleIndex(client, store)(strategy, chunks, batch_size=args.batch_size)
            logger.info(
                "%s: %d chunks, %d embedded, %d input tokens, cache now %d vectors",
                strategy,
                counts.chunks,
                counts.embedded,
                counts.input_tokens,
                cache.count(),
            )
    finally:
        store.close()
        cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
