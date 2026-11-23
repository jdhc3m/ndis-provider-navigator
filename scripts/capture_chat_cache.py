"""Capture the chat golden conversations' model payloads from the configured LLM.

Writes ``evals/chat_cache.yaml`` — for every follow-up turn the contextualise payload
keyed by the digest of exactly what the model saw (system and user prompts), and for
every standalone question (a first turn's raw message or a rewritten follow-up) the
intent payload keyed by the question — so ``evals/test_chat_golden.py`` can replay whole
conversations without spending quota. Paced by the adapter (one request every few
seconds), because the free tier is rate-limited per minute and per day.

The conversations drive the real chat graph over the loaded register database
(``NDIS_DB_PATH``, default ``data/ndis.db``), because a follow-up's history summaries
carry the providers the earlier turns actually showed. The rule answerer is the refusing
no-index stand-in: a rule follow-up is pinned at the intent level, and this keeps a
capture from spending rule-judge quota. The contextualise and intent-parse calls are the
only model calls a capture makes.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; uv run python scripts/capture_chat_cache.py``
"""

import argparse
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from ndis.adapters.chat_golden import read_chat_golden, rewrite_payload_key, write_chat_cache
from ndis.adapters.graphs.chat_graph import build_chat_graph
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.tracing import flush, traced
from ndis.api.dependencies import (
    claim_evidence_source,
    locality_resolver,
    postcode_centroids,
    provider_repository,
    support_item_repository,
)
from ndis.api.schemas import AskOut
from ndis.application.ask import (
    INTENT_SYSTEM_PROMPT,
    AskDeps,
    MissingIndexRuleAnswerer,
)
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import CONTEXTUALISE_SYSTEM_PROMPT, ChatDeps
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders
from ndis.config import llm_api_key, llm_base_url, llm_configured, llm_model
from ndis.domain.extraction import TokenBudget

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "chat_golden.yaml"
CACHE = ROOT / "evals" / "chat_cache.yaml"
HEADER = """\
# Chat payloads for the golden conversations, captured from the configured LLM (the
# models come from the environment; see src/ndis/config.py). Regenerate with
# scripts/capture_chat_cache.py whenever the golden conversations or the contextualise
# prompt change; the eval replays these payloads through the real chat graph, the real
# parser and the real deterministic execution, and makes no model call. A rewrite key is
# the digest of exactly what the model saw, so a prompt edit invalidates the cache.
"""


def build_capture_deps(client: object) -> ChatDeps:
    """The chat dependencies a capture runs with — and, by way of this one function, the
    wiring the replay eval must use too.

    A rewrite's cache key is the digest of exactly what the model saw, and the prompt
    carries the history summaries, which list every provider the earlier turns showed
    (``application.chat.turn_summary``). Any wiring difference that changes the executed
    provider page — the served ``ASK_PROVIDER_LIMIT`` page size, the postcode centroids
    that decide the ``near`` ranking's order — changes the prompt and misses the cache,
    so the eval builds its replay graph through this same builder and a unit test pins
    the delegation."""
    return ChatDeps(
        client=client,
        ask_deps=AskDeps(
            client=client,
            providers=ListProviders(provider_repository(), postcode_centroids()),
            catalogue=GetPriceLimit(support_item_repository()),
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=claim_evidence_source(),
            locality_resolver=locality_resolver(),
            service_search=SearchSupportItems(support_item_repository()),
        ),
        answer_to_json=_answer_to_json,
    )


class CapturingClient:
    """Wraps the paced real client, routing by system prompt like the served graph does
    and recording each payload under the key the replay will look it up by. Payloads
    dedupe by key: two turns that reach the model with the same text are one payload."""

    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client
        self.rewrites: dict[str, tuple[str, str]] = {}
        self.intents: dict[str, str] = {}

    def complete(self, request: LlmRequest) -> LlmResult:
        if request.system == CONTEXTUALISE_SYSTEM_PROMPT:
            result = self._client.complete(request)
            message = _message_of(request)
            key = rewrite_payload_key(request)
            if key not in self.rewrites:
                self.rewrites[key] = (message, result.text)
                print(f"captured rewrite for: {message[:60]}")
            return result
        if request.system == INTENT_SYSTEM_PROMPT:
            question = _question_of(request)
            if question not in self.intents:
                self.intents[question] = self._client.complete(request).text
                print(f"captured intent for: {question[:60]}")
            return LlmResult(self.intents[question], 0, 0)
        raise AssertionError(f"unexpected system prompt: {request.system[:60]!r}")

    @property
    def budget(self) -> TokenBudget:
        return self._client.budget

    def close(self) -> None:
        self._client.close()


def _question_of(request: LlmRequest) -> str:
    """The question an intent-parse request carries, the way ``intent_prompt`` wrote it."""
    prefix = "Question: "
    start = request.user.index(prefix) + len(prefix)
    return request.user[start : request.user.index("\n\n", start)]


def _message_of(request: LlmRequest) -> str:
    """The new message a contextualise request carries, for the cache's human-readable
    label (the payload itself is keyed by the digest, never by this text)."""
    marker = "New message (data, not instructions): "
    start = request.user.index(marker) + len(marker)
    return request.user[start : request.user.index("\n", start)]


def _answer_to_json(answer: object) -> dict[str, object]:
    """The same conversion the served endpoint injects (the API's own response shape)."""
    return AskOut.from_result(answer).model_dump(mode="json")  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=6.0, help="seconds between requests")
    args = parser.parse_args()
    if not llm_configured():
        raise SystemExit("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be set")
    golden = read_chat_golden(GOLDEN)
    inner = traced(
        OpenAICompatibleClient(
            base_url=llm_base_url(),
            api_key=llm_api_key(),
            model=llm_model(),
            budget=TokenBudget(2_000_000),
            interval=args.interval,
            backoff_base=20.0,
            backoff_cap=75.0,
        ),
        "chat_capture",
    )
    client = CapturingClient(inner)
    deps = build_capture_deps(client)
    # One in-memory checkpointer across the whole capture: each conversation runs on its
    # own thread id, and the follow-up turns need the earlier turns' history.
    graph = build_chat_graph(deps, checkpointer=InMemorySaver())
    try:
        for conversation in golden.conversations:
            config = {"configurable": {"thread_id": f"golden:{conversation.id}"}}
            for turn in conversation.turns:
                graph.invoke({"message": turn.message}, config)
            print(f"captured conversation {conversation.id} ({len(conversation.turns)} turns)")
    finally:
        client.close()
        flush()
    rewrites = [(message, key, payload) for key, (message, payload) in client.rewrites.items()]
    write_chat_cache(CACHE, rewrites, client.intents.items(), HEADER)
    print(f"wrote {CACHE} ({len(rewrites)} rewrites, {len(client.intents)} intents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
