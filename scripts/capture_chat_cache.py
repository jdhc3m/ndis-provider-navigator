"""Capture the chat golden conversations' model payloads from the configured LLM.

Writes ``evals/chat_cache.yaml`` — for every follow-up turn the contextualise payload
keyed by the digest of exactly what the model saw (system and user prompts), for every
standalone question (a first turn's raw message or a rewritten follow-up) the intent
payload keyed by the question, and for every conversation turn the turn summary exactly
as ``application.chat.contextualise_stage`` built it (keyed by the turn's standalone
question, which the summary carries) — so ``evals/test_chat_golden.py`` can replay whole
conversations without spending quota and without executing the earlier turns the way
capture time did: the rewrite prompts' history comes verbatim from the summaries.
Paced by the adapter (one request every few seconds), because the free tier is
rate-limited per minute and per day.

The conversations drive the real chat graph over the loaded register database
(``NDIS_DB_PATH``, default ``data/ndis.db``), because a follow-up's history summaries
carry the providers the earlier turns actually showed. The rule answerer is the refusing
no-index stand-in: a rule follow-up is pinned at the intent level, and this keeps a
capture from spending rule-judge quota. The contextualise and intent-parse calls are the
only model calls a capture makes.

A turn whose golden row names a ``choose`` id pauses the graph on the ambiguous locality
(ticket 18) and is resumed with that id, exactly as the replay answers it.

``--only id[,id...]`` captures only the named conversations and merges their payloads
into the existing cache, so adding golden conversations does not recapture (and cannot
drift) the existing ones.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; uv run python scripts/capture_chat_cache.py``
"""

import argparse
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from ndis.adapters.chat_golden import (
    ChatCache,
    ChatGoldenSet,
    read_chat_cache,
    read_chat_golden,
    rewrite_payload_key,
    write_chat_cache,
)
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
from ndis.application.chat import CONTEXTUALISE_SYSTEM_PROMPT, ChatDeps, turn_summary
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
# the digest of exactly what the model saw, so a prompt edit invalidates the cache. The
# summaries section pins the rewrite prompts' history (standalone question, parsed
# intent, providers shown): the replay rebuilds the history from it byte-identically, so
# its own execution — which runs elsewhere — cannot change what the model saw.
"""


def build_capture_deps(client: object) -> ChatDeps:
    """The chat dependencies a capture runs with, at the served pipeline's wiring: the
    same ``ASK_PROVIDER_LIMIT`` provider page the live /chat endpoint executes, and the
    postcode centroids that decide the ``near`` ranking's order, so a capture sees what
    a user would see.

    A rewrite's cache key is the digest of exactly what the model saw, and the prompt
    carries the history summaries, which list every provider the earlier turns showed
    (``application.chat.turn_summary``). The replay eval no longer shares this builder:
    its history comes verbatim from the cache's summaries section, so its prompts are
    byte-identical to capture time whatever it executes downstream (a unit test pins
    that invariance instead of the old delegation)."""
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


def _pending_of(graph: object, config: dict[str, object]) -> dict[str, object] | None:
    """The pending question the thread's paused task waits on, or ``None``."""
    snapshot = graph.get_state(config)  # type: ignore[attr-defined]
    for task in snapshot.tasks:
        for item in task.interrupts:
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("kind") == "choose_locality":
                return value
    return None


def turn_final(graph: object, config: dict[str, object], turn: object) -> dict[str, object]:
    """One conversation turn's final graph state: the turn's answer — or, when the turn
    paused on an ambiguous locality (ticket 18), the resumed one. ``choose`` names the
    option id the golden file answers the pending question with."""
    final = graph.invoke({"message": turn.message}, config)  # type: ignore[attr-defined]
    if turn.choose is not None:
        pending = _pending_of(graph, config)
        if pending is None:
            raise SystemExit(
                f"the turn {turn.message!r} names a choice but the graph did not pause"
            )
        final = graph.invoke(Command(resume=turn.choose), config)  # type: ignore[attr-defined]
        if _pending_of(graph, config) is not None:
            raise SystemExit(
                f"the turn {turn.message!r} is still pending after resuming {turn.choose!r}"
            )
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=6.0, help="seconds between requests")
    parser.add_argument(
        "--only",
        default="",
        help="comma-separated conversation ids to capture; the payloads are merged into "
        "the existing cache instead of recapturing every conversation",
    )
    args = parser.parse_args()
    if not llm_configured():
        raise SystemExit("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be set")
    golden = read_chat_golden(GOLDEN)
    if args.only:
        wanted = [name.strip() for name in args.only.split(",") if name.strip()]
        known = {conversation.id for conversation in golden.conversations}
        missing = [name for name in wanted if name not in known]
        if missing:
            raise SystemExit(f"no such conversation(s) in {GOLDEN.name}: {missing}")
        golden = ChatGoldenSet(
            conversations=tuple(
                conversation for conversation in golden.conversations if conversation.id in wanted
            )
        )
    # An --only capture merges into the existing cache: the conversations it does not
    # re-run keep their captured payloads, so quota is spent on the new turns alone.
    existing = read_chat_cache(CACHE) if CACHE.is_file() else ChatCache()
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
    summaries: dict[str, dict[str, object]] = dict(existing.summaries)
    try:
        for conversation in golden.conversations:
            config = {"configurable": {"thread_id": f"golden:{conversation.id}"}}
            for turn in conversation.turns:
                final = turn_final(graph, config, turn)
                stored = final["turns"][-1]
                # The turn summary exactly as the rewrite stage built it; identical
                # standalone questions (the same question never produced two different
                # pages) dedupe to the first recording.
                summaries.setdefault(stored["standalone_question"], turn_summary(stored))
            print(f"captured conversation {conversation.id} ({len(conversation.turns)} turns)")
    finally:
        client.close()
        flush()
    rewrites: dict[str, tuple[str, str]] = {
        key: (existing.rewrite_messages[key], existing.rewrites[key]) for key in existing.rewrites
    }
    rewrites.update(client.rewrites)
    intents: dict[str, str] = dict(existing.intents)
    intents.update(client.intents)
    ordered = [(message, key, payload) for key, (message, payload) in rewrites.items()]
    write_chat_cache(CACHE, ordered, intents.items(), HEADER, summaries=summaries)
    print(
        f"wrote {CACHE} ({len(ordered)} rewrites, {len(intents)} intents, "
        f"{len(summaries)} summaries)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
