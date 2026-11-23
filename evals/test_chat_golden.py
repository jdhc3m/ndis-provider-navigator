"""The chat golden eval: the multi-turn conversations of ``evals/chat_golden.yaml``
replayed from ``evals/chat_cache.yaml`` through the real chat graph, intent parser and
deterministic execution — zero model calls.

The cache holds, per follow-up turn, the contextualise payload captured for exactly what
the model saw (keyed by a digest of the system and user prompts, so a prompt edit
invalidates the cache instead of replaying a stale rewrite), and per standalone question
the intent payload captured for it. The replay drives the real graph turn by turn on one
thread per conversation: a rewrite payload is looked up by digest, the standalone
question goes to the intent parser, the parsed intent executes deterministically, and
the stored turn's final intent (or refusal) is compared with the golden expectation.

Two floors are enforced from ``evals/thresholds.yaml``: ``followup_accuracy`` (follow-up
turns whose final parsed intent matches the expectation) and ``followup_refusal_recall``
(follow-up decision requests and injections still contained with the expected reason).
First turns are asserted exactly, like the locality cases of the /ask golden eval. The
run skips visibly while the cache or the register database is missing on this machine;
the database file comes from ``NDIS_DB_PATH`` (see ``ndis.api.dependencies``).

The rule answerer is the refusing no-index stand-in on both the capture and the replay:
a rule follow-up is pinned at the intent level here, and this keeps a capture from
spending rule-judge quota.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.chat_golden import (
    ChatCache,
    ChatGoldenSet,
    GoldenTurn,
    read_chat_cache,
    read_chat_golden,
    rewrite_payload_key,
)
from ndis.adapters.graphs.chat_graph import build_chat_graph
from ndis.adapters.sqlite_repository import (
    SqlAlchemyPostcodeCentroids,
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.adapters.thresholds import read_thresholds
from ndis.api.schemas import AskOut
from ndis.application.ask import (
    INTENT_SYSTEM_PROMPT,
    AskDeps,
    MissingIndexRuleAnswerer,
)
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import CONTEXTUALISE_SYSTEM_PROMPT, ChatDeps
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.extraction import TokenBudget
from ndis.domain.intent import ProviderFilters

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "chat_golden.yaml"
CACHE = ROOT / "evals" / "chat_cache.yaml"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"
DB_PATH_ENV = "NDIS_DB_PATH"
DB = Path(os.environ.get(DB_PATH_ENV) or ROOT / "data" / "ndis.db")
EXTRACTION = ROOT / "data" / "private" / "extraction"
PAGES = ROOT / "data" / "private" / "pages"
FILTER_PAGE = 5
GOLDEN_ROOT_NS = "golden:"  # thread-id prefix, one thread per conversation


class ReplayChatClient:
    """A fake ``LlmClient`` that replays the captured payloads: rewrite requests by the
    digest of what the model saw, intent requests by the standalone question. A missing
    payload is recorded and answered with an empty string, so the run degrades to a
    recorded failure instead of a crash."""

    def __init__(self, rewrites: dict[str, str], intents: dict[str, str]) -> None:
        self._rewrites = dict(rewrites)
        self._intents = dict(intents)
        self.missing: list[str] = []

    def complete(self, request: LlmRequest) -> LlmResult:
        if request.system == CONTEXTUALISE_SYSTEM_PROMPT:
            key = rewrite_payload_key(request)
            payload = self._rewrites.get(key)
            if payload is None:
                self.missing.append(f"rewrite {key[:12]} for {request.user!r}")
                return LlmResult("", 0, 0)
            return LlmResult(payload, 0, 0)
        if request.system == INTENT_SYSTEM_PROMPT:
            question = _question_of(request)
            payload = self._intents.get(question)
            if payload is None:
                self.missing.append(f"intent for {question!r}")
                return LlmResult("", 0, 0)
            return LlmResult(payload, 0, 0)
        raise AssertionError(f"unexpected system prompt: {request.system[:60]!r}")

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(0)


def _question_of(request: LlmRequest) -> str:
    """The question an intent-parse request carries, the way ``intent_prompt`` wrote it."""
    prefix = "Question: "
    start = request.user.index(prefix) + len(prefix)
    return request.user[start : request.user.index("\n\n", start)]


@dataclass
class ReplayOutcome:
    """What one replay pass scored: the follow-up floors' numerators and denominators,
    the first-turn mismatches, the per-problem failure lines, and any cache misses."""

    followup_total: int = 0
    followup_hits: int = 0
    refusal_total: int = 0
    refusal_hits: int = 0
    first_turn_failures: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _intent_of(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The stored answer's parsed intent, in the API's own shape (empty when refused
    before anything parsed)."""
    return (answer or {}).get("intent") or {}


def _filters_of(intent: Mapping[str, Any]) -> ProviderFilters | None:
    """The ``ProviderFilters`` the stored answer's intent carries (the API's own shape)."""
    filters = intent.get("provider_filters")
    if filters is None:
        return None
    return ProviderFilters(
        state=filters.get("state"),
        postcode=filters.get("postcode"),
        locality=filters.get("locality"),
        group=filters.get("group"),
        status=filters.get("status"),
        brisbane_area=filters.get("brisbane_area"),
    )


def _compare_expectation(turn: GoldenTurn, answer: Mapping[str, Any]) -> list[str]:
    """The ways one stored answer fails its turn's expectation, as short problem strings."""
    intent = _intent_of(answer)
    problems: list[str] = []
    if turn.filters is not None:
        if _filters_of(intent) != turn.filters:
            problems.append(f"filters:{_filters_of(intent)}")
    if turn.service is not None:
        price = intent.get("price_query")
        if price is None:
            problems.append("no_price_query")
        else:
            if price.get("service") != turn.service:
                problems.append(f"service:{price.get('service')!r}")
            if price.get("item") is not None:
                problems.append(f"item:{price.get('item')}")
            if price.get("state") != turn.service_state:
                problems.append(f"state:{price.get('state')}")
        if _filters_of(intent) is not None:
            problems.append("provider_filters_leaked")
    if turn.rule_contains is not None:
        rule = intent.get("rule_question")
        if not isinstance(rule, str) or turn.rule_contains.casefold() not in rule.casefold():
            problems.append(f"rule_question:{rule!r}")
        if _filters_of(intent) is not None:
            problems.append("provider_filters_leaked")
    return problems


def run_replay(graph: Any, golden: ChatGoldenSet, client: ReplayChatClient) -> ReplayOutcome:
    """Run every golden conversation on its own thread and score the outcome; any cache
    miss the replay client hit is carried in the outcome."""
    outcome = ReplayOutcome()
    for conversation in golden.conversations:
        config = {"configurable": {"thread_id": GOLDEN_ROOT_NS + conversation.id}}
        for index, turn in enumerate(conversation.turns):
            final = graph.invoke({"message": turn.message}, config)
            stored = final["turns"][-1]
            answer: dict[str, Any] = stored["answer"]
            if index == 0:
                problems = _compare_expectation(turn, answer)
                outcome.first_turn_failures.extend(
                    f"{conversation.id} turn 1: {problem}" for problem in problems
                )
                continue
            if turn.refusal is not None:
                outcome.refusal_total += 1
                if answer.get("refused") is True and answer.get("reason") == turn.refusal:
                    outcome.refusal_hits += 1
                else:
                    outcome.failures.append(
                        f"{conversation.id} turn {index + 1}: "
                        f"expected refusal {turn.refusal}, got {answer.get('reason')!r}"
                    )
                continue
            problems = _compare_expectation(turn, answer)
            outcome.followup_total += 1
            if problems:
                outcome.failures.append(
                    f"{conversation.id} turn {index + 1}: {', '.join(problems)}"
                )
            else:
                outcome.followup_hits += 1
    outcome.missing = list(client.missing)
    return outcome


def build_replay_graph(cache: ChatCache) -> tuple[Any, ReplayChatClient]:
    """The real chat graph over the loaded register database, replaying the cache."""
    engine = create_sqlite_engine(DB)
    client = ReplayChatClient(cache.rewrites, cache.intents)
    deps = ChatDeps(
        client=client,
        ask_deps=AskDeps(
            client=client,
            providers=ListProviders(SqlAlchemyProviderRepository(engine)),
            catalogue=GetPriceLimit(SqlAlchemySupportItemRepository(engine)),
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=ExtractionEvidenceSource(engine, EXTRACTION, PAGES),
            locality_resolver=LocalityResolver(SqlAlchemyPostcodeCentroids(engine)),
            service_search=SearchSupportItems(SqlAlchemySupportItemRepository(engine)),
            provider_limit=FILTER_PAGE,
        ),
        answer_to_json=_answer_to_json,
    )
    return build_chat_graph(deps, checkpointer=InMemorySaver()), client


def _answer_to_json(answer: Any) -> dict[str, Any]:
    """The composed answer in exactly the shape the API returns (the composition root
    injects the same conversion for the served endpoint)."""
    return AskOut.from_result(answer).model_dump(mode="json")


@pytest.fixture
def replay() -> tuple[Any, ReplayChatClient, ChatGoldenSet]:
    if not CACHE.is_file():
        pytest.skip(f"chat cache not captured yet: {CACHE} is missing")
    if not DB.is_file():
        pytest.skip(f"register database not loaded yet: {DB} is missing")
    golden = read_chat_golden(GOLDEN)
    graph, client = build_replay_graph(read_chat_cache(CACHE))
    return graph, client, golden


@pytest.mark.eval
def test_first_turns_match_the_expectations_exactly(
    replay: tuple[Any, ReplayChatClient, ChatGoldenSet],
) -> None:
    graph, client, golden = replay
    outcome = run_replay(graph, golden, client)
    assert not outcome.missing, "\n".join(outcome.missing)
    assert not outcome.first_turn_failures, "\n".join(outcome.first_turn_failures)


@pytest.mark.eval
def test_followup_turns_match_the_expectations_at_the_floor(
    replay: tuple[Any, ReplayChatClient, ChatGoldenSet],
) -> None:
    graph, client, golden = replay
    outcome = run_replay(graph, golden, client)
    assert not outcome.missing, "\n".join(outcome.missing)
    # The floor's own denominator: the golden set must keep its follow-up expectations,
    # or the accuracy below would be a vacuous 1.0.
    assert outcome.followup_total >= 10, outcome.followup_total
    floor = read_thresholds(THRESHOLDS).followup_accuracy
    accuracy = outcome.followup_hits / outcome.followup_total if outcome.followup_total else 1.0
    assert accuracy >= floor, "\n".join(outcome.failures)


@pytest.mark.eval
def test_followup_refusals_are_contained(
    replay: tuple[Any, ReplayChatClient, ChatGoldenSet],
) -> None:
    graph, client, golden = replay
    outcome = run_replay(graph, golden, client)
    assert not outcome.missing, "\n".join(outcome.missing)
    # The refusal floor's denominator: the decision-request and injection follow-ups
    # must both stay in the golden set.
    assert outcome.refusal_total >= 2, outcome.refusal_total
    floor = read_thresholds(THRESHOLDS).followup_refusal_recall
    recall = outcome.refusal_hits / outcome.refusal_total if outcome.refusal_total else 1.0
    assert recall >= floor, "\n".join(outcome.failures)
