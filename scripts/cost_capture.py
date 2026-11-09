"""Measure real per-call latency and token usage for the cost report.

Runs a small, paced set of real calls through the recording client — a few golden
intent parses, the golden questions embedded in one batch that bypasses the cache, and
a few stored pages through the extraction prompt — and appends every measurement to
``data/private/cost/``. The judge/answer run is measured separately by
``scripts/build_judge_verdicts.py`` while it generates the verdict template. Nothing is
written outside the private cost directory.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; LLM_MODEL=... uv run python
-m scripts.cost_capture [--samples 3] [--interval SECONDS]``
"""

import argparse
import sys
from pathlib import Path

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.golden_questions import read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.adapters.sqlite_repository import create_sqlite_engine
from ndis.adapters.stored_pages import FilePageTextSource
from ndis.adapters.tracing import flush, traced
from ndis.application.ask import INTENT_SYSTEM_PROMPT, intent_prompt
from ndis.application.extraction import EXTRACTION_SYSTEM_PROMPT, MAX_PAGE_CHARS, LlmRequest
from ndis.application.extraction import page_prompt as extraction_page_prompt
from ndis.application.pricing import AskRule
from ndis.config import (
    embed_model,
    llm_api_key,
    llm_base_url,
    llm_configured,
    llm_model,
    rule_chunk_strategy,
    rule_similarity_threshold,
    rule_top_k,
)
from ndis.domain.extraction import TokenBudget, page_excerpt
from scripts.recording import RecordingClient, run_log

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
INDEX = ROOT / "data" / "rule_index.db"
CACHE = ROOT / "data" / "private" / "embedding_cache.db"
PAGES_DB = ROOT / "data" / "ndis.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3, help="calls per measured endpoint")
    parser.add_argument("--interval", type=float, default=7.0)
    args = parser.parse_args()
    if not llm_configured() or not embed_model():
        raise SystemExit("LLM_* and EMBED_MODEL must be set (source ~/.config/ndis/.env)")
    if not PAGES_DB.is_file():
        raise SystemExit(f"register database missing: {PAGES_DB}")

    log = run_log("endpoints")
    real = OpenAICompatibleClient(
        base_url=llm_base_url(),
        api_key=llm_api_key(),
        model=llm_model(),
        embed_model=embed_model(),
        budget=TokenBudget(1_000_000),
        interval=args.interval,
        backoff_base=20.0,
        backoff_cap=75.0,
    )
    golden = read_golden(GOLDEN)
    engine = create_sqlite_engine(PAGES_DB)
    source = FilePageTextSource(engine, ROOT / "data" / "private" / "pages")
    try:
        intent = traced(RecordingClient(real, "intent_parse", log), "intent_parse")
        for question in [case.question for case in golden.filter_cases[: args.samples]]:
            result = intent.complete(LlmRequest(INTENT_SYSTEM_PROMPT, intent_prompt(question)))
            print(f"intent_parse: {result.input_tokens} in / {result.output_tokens} out")

        embeddings = traced(RecordingClient(real, "rule_embedding", log), "rule_embedding")
        batch = [question.question for question in golden.questions]
        embeddings_result = embeddings.embed(batch)
        vectors, tokens = len(embeddings_result.vectors), embeddings_result.input_tokens
        print(f"rule_embedding: {vectors} vectors, {tokens} in")
        embeddings.embed([golden.off_topic_question])
        print("rule_embedding: off-topic question embedded")

        extraction = traced(RecordingClient(real, "extraction", log), "extraction")
        seen: set[str] = set()
        pages: list = []
        for page in source.ok_pages():
            if page.provider_key in seen:
                continue  # one page per provider, so the sample spreads across sites
            seen.add(page.provider_key)
            pages.append(page)
            if len(pages) >= args.samples:
                break
        for page in pages:
            request = LlmRequest(
                EXTRACTION_SYSTEM_PROMPT,
                extraction_page_prompt(page_excerpt(page.text, MAX_PAGE_CHARS)),
            )
            result = extraction.complete(request)
            print(f"extraction: {result.input_tokens} in / {result.output_tokens} out")

        # The rule judge's latency re-measured on a few golden questions: the generation
        # run that produced the verdict template recorded wall-clock including the
        # pacing wait, so its rows price the tokens but not the endpoint speed.
        store = SqliteVecRuleStore(INDEX)
        cache = EmbeddingCache(CACHE)
        judge = AskRule(
            CachedEmbeddingClient(embeddings, cache, embed_model()),
            store,
            traced(RecordingClient(real, "rule_judge", log), "rule_judge"),
            strategy=rule_chunk_strategy(),
            threshold=rule_similarity_threshold(),
            top_k=rule_top_k(),
        )
        try:
            for question in golden.questions[: args.samples]:
                result = judge(question.question)
                if not result.refused:
                    print(f"rule_judge: answered ({len(result.citations)} citations)")
                else:
                    print(f"rule_judge: refused ({result.reason})")
        finally:
            store.close()
            cache.close()
        print(f"recorded to {log}")
    finally:
        real.close()
        flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
