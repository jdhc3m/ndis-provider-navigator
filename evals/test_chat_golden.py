"""The chat golden eval: the multi-turn conversations of ``evals/chat_golden.yaml``
replayed from ``evals/chat_cache.yaml`` through the real chat graph, intent parser and
deterministic execution — zero model calls, zero register queries.

The cache holds, per follow-up turn, the contextualise payload captured for exactly what
the model saw (keyed by a digest of the system and user prompts, so a prompt edit
invalidates the cache instead of replaying a stale rewrite), per standalone question the
intent payload captured for it, and per conversation turn the summary the rewrite prompt
carried for it. The replay drives the real graph turn by turn on one thread per
conversation: a rewrite payload is looked up by digest, the standalone question goes to
the intent parser, the parsed intent executes deterministically, and the stored turn's
final intent (or refusal) is compared with the golden expectation.

The rewrite prompts' history is rebuilt exclusively from the cached summaries, so it is
byte-identical to capture time no matter what the replay executes downstream: the ask
sections run against small seeded fixture repositories (a temp SQLite file, the same
shape the unit tests seed), because the replayed questions' providers are irrelevant —
only the parsed intents and refusals are asserted — and the real register must not be
queried. (Before the summaries existed the replay had to share the capture's wiring
down to the provider page size and centroids; the cache now pins that.)

Two floors are enforced from ``evals/thresholds.yaml``: ``followup_accuracy`` (follow-up
turns whose final parsed intent matches the expectation) and ``followup_refusal_recall``
(follow-up decision requests and injections still contained with the expected reason).
First turns are asserted exactly, like the locality cases of the /ask golden eval. The
run skips visibly while the cache file is missing; everything else it needs is committed
or seeded here.

The rule answerer is the refusing no-index stand-in on both the capture and the replay:
a rule follow-up is pinned at the intent level here, and this keeps a capture from
spending rule-judge quota.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

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
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.adapters.thresholds import read_thresholds
from ndis.api.schemas import AskOut
from ndis.application.ask import INTENT_SYSTEM_PROMPT, AskDeps, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import CONTEXTUALISE_SYSTEM_PROMPT, ChatDeps, TurnSummarizer
from ndis.application.extraction import LlmClient, LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.extraction import TokenBudget
from ndis.domain.intent import ProviderFilters
from ndis.domain.locality import Locality
from tests.conftest import SEED_PROVIDERS, SEED_SUPPORT_ITEMS, LocalityDirectoryStub

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "chat_golden.yaml"
CACHE = ROOT / "evals" / "chat_cache.yaml"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"
GOLDEN_ROOT_NS = "golden:"  # thread-id prefix, one thread per conversation

# The gazetteer rows the replay resolves against: the real one spells Paddington twice
# (QLD 4064 and NSW 2021), and the paused golden turns must be offered exactly these two
# candidates — the same option ids the served graph would offer.
PADDINGTON_ROWS = (
    Locality(name="PADDINGTON", state="QLD", postcode="4064"),
    Locality(name="PADDINGTON", state="NSW", postcode="2021"),
)
PADDINGTON_OPTIONS = [
    {"id": "nsw-2021", "label": "NSW 2021"},
    {"id": "qld-4064", "label": "QLD 4064"},
]


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


class CachedSummarizer:
    """The replay's history source: the captured turn summaries, keyed by the turn's
    standalone question — the same question always answered the same way at capture
    time, so the question identifies the summary. A turn without one is recorded and
    summarized empty, so the run degrades to a recorded failure (the rewrite prompt
    then differs from capture time and its lookup misses) instead of a crash."""

    def __init__(self, summaries: Mapping[str, Mapping[str, Any]]) -> None:
        self._summaries = dict(summaries)
        self.missing: list[str] = []

    @property
    def known_questions(self) -> frozenset[str]:
        """The standalone questions the cache holds summaries for."""
        return frozenset(self._summaries)

    def __call__(self, turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for turn in turns:
            question = str(turn.get("standalone_question") or turn.get("message", ""))
            summary = self._summaries.get(question)
            if summary is None:
                self.missing.append(f"summary for {question!r}")
                summary = {"standalone_question": question, "intent": None, "providers_shown": []}
            summaries.append(dict(summary))
        return summaries


@dataclass
class ReplayOutcome:
    """What one replay pass scored: the follow-up floors' numerators and denominators,
    the first-turn mismatches, the per-problem failure lines, any cache misses, and the
    standalone questions the replayed turns actually ended with."""

    followup_total: int = 0
    followup_hits: int = 0
    refusal_total: int = 0
    refusal_hits: int = 0
    first_turn_failures: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    consumed_questions: frozenset[str] = field(default_factory=frozenset)


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
    if turn.rule_contains is not None:
        rule = intent.get("rule_question")
        if not isinstance(rule, str) or turn.rule_contains.casefold() not in rule.casefold():
            problems.append(f"rule_question:{rule!r}")
    # A price or rule follow-up that switched away from providers must not keep filters —
    # unless the turn expects them: the rewrite copies the place forward, so a service
    # follow-up may legitimately carry the combined shape (filters plus the price query).
    switched_away = turn.service is not None or turn.rule_contains is not None
    if switched_away and turn.filters is None and _filters_of(intent) is not None:
        problems.append("provider_filters_leaked")
    return problems


def _pending_of(graph: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """The pending question the thread's paused task waits on, or ``None``."""
    snapshot = graph.get_state(config)
    for task in snapshot.tasks:
        for item in task.interrupts:
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("kind") == "choose_locality":
                return value
    return None


def run_replay(
    graph: Any, golden: ChatGoldenSet, client: ReplayChatClient, summarizer: CachedSummarizer
) -> ReplayOutcome:
    """Run every golden conversation on its own thread and score the outcome; any cache
    miss the replay client or the summarizer hit is carried in the outcome, along with
    every replayed turn's standalone question so the caller can pin full summary
    consumption (``consumed_questions`` against :attr:`CachedSummarizer.known_questions`
    flags both orphaned rows and consumed questions without a row).

    A turn that names a ``choose`` id pauses the graph (the ambiguous-locality question
    of ticket 18): the replay asserts the pause carried exactly the Paddington options,
    resumes with the golden choice, and compares the resumed turn like any other."""
    outcome = ReplayOutcome()
    consumed: set[str] = set()
    for conversation in golden.conversations:
        config = {"configurable": {"thread_id": GOLDEN_ROOT_NS + conversation.id}}
        for index, turn in enumerate(conversation.turns):
            final = graph.invoke({"message": turn.message}, config)
            if turn.choose is not None:
                payload = _pending_of(graph, config)
                if payload is None:
                    outcome.failures.append(
                        f"{conversation.id} turn {index + 1}: expected the locality "
                        "question to pause, nothing is pending"
                    )
                    continue
                if payload.get("options") != PADDINGTON_OPTIONS:
                    outcome.failures.append(
                        f"{conversation.id} turn {index + 1}: unexpected options "
                        f"{payload.get('options')!r}"
                    )
                    continue
                final = graph.invoke(Command(resume=turn.choose), config)
                if _pending_of(graph, config) is not None:
                    outcome.failures.append(
                        f"{conversation.id} turn {index + 1}: still pending after "
                        f"resuming {turn.choose!r}"
                    )
                    continue
            stored = final["turns"][-1]
            consumed.add(str(stored["standalone_question"]))
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
    outcome.missing = [*client.missing, *summarizer.missing]
    outcome.consumed_questions = frozenset(consumed)
    return outcome


class ReplayEvidence:
    """A ``ClaimEvidenceSource`` with nothing cached: the replayed questions' providers
    are fixtures, so there is no page to quote and the evidence stage runs empty."""

    def claims_for(self, provider_keys: Sequence[str]) -> dict[str, tuple]:  # type: ignore[type-arg]
        return {key: () for key in provider_keys}

    def page_text(self, provider_key: str, url: str) -> str:
        del provider_key, url
        return ""


@dataclass(frozen=True)
class ReplayRepos:
    """The seeded repositories the replay's ask sections execute against."""

    providers: SqlAlchemyProviderRepository
    catalogue: SqlAlchemySupportItemRepository


def seed_replay_repos(tmp_path: Path) -> ReplayRepos:
    """The temp SQLite repositories the replay executes over: the same seeds the unit
    tests use, so the pipeline runs end to end without the register database."""
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    providers = SqlAlchemyProviderRepository(engine)
    providers.replace_all(SEED_PROVIDERS, [])
    catalogue = SqlAlchemySupportItemRepository(engine)
    catalogue.replace_all(SEED_SUPPORT_ITEMS)
    return ReplayRepos(providers=providers, catalogue=catalogue)


def fixture_chat_deps(
    client: LlmClient,
    repos: ReplayRepos,
    *,
    summarize_turns: TurnSummarizer | None = None,
) -> ChatDeps:
    """The replay's chat dependencies over the seeded repositories. Nothing here can
    touch the rewrite prompts any more: their history comes from the cache's summaries,
    so whatever executes downstream of the contextualise stage cannot drift a digest."""
    return ChatDeps(
        client=client,
        ask_deps=AskDeps(
            client=client,
            providers=ListProviders(repos.providers),
            catalogue=GetPriceLimit(repos.catalogue),
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=ReplayEvidence(),  # type: ignore[arg-type]
            locality_resolver=LocalityResolver(LocalityDirectoryStub(PADDINGTON_ROWS)),
            service_search=SearchSupportItems(repos.catalogue),
        ),
        answer_to_json=lambda answer: AskOut.from_result(answer).model_dump(mode="json"),
        summarize_turns=summarize_turns,
    )


def build_replay_deps(
    cache: ChatCache, repos: ReplayRepos
) -> tuple[ChatDeps, ReplayChatClient, CachedSummarizer]:
    """The replay's dependencies: the fixture wiring with the cached summaries injected
    as the rewrite history's source (the served default,
    :func:`ndis.application.chat.default_turn_summaries`, is what capture time ran)."""
    client = ReplayChatClient(cache.rewrites, cache.intents)
    summarizer = CachedSummarizer(cache.summaries)
    deps = fixture_chat_deps(client, repos, summarize_turns=summarizer)
    return deps, client, summarizer


def build_replay_graph(
    cache: ChatCache, repos: ReplayRepos
) -> tuple[Any, ReplayChatClient, CachedSummarizer]:
    """The real chat graph over the seeded repositories, replaying the cache."""
    deps, client, summarizer = build_replay_deps(cache, repos)
    return build_chat_graph(deps, checkpointer=InMemorySaver()), client, summarizer


@pytest.fixture
def replay(
    tmp_path: Path,
) -> tuple[Any, ReplayChatClient, CachedSummarizer, ChatGoldenSet]:
    if not CACHE.is_file():
        pytest.skip(f"chat cache not captured yet: {CACHE} is missing")
    golden = read_chat_golden(GOLDEN)
    graph, client, summarizer = build_replay_graph(
        read_chat_cache(CACHE), seed_replay_repos(tmp_path)
    )
    return graph, client, summarizer, golden


@pytest.mark.eval
def test_first_turns_match_the_expectations_exactly(
    replay: tuple[Any, ReplayChatClient, CachedSummarizer, ChatGoldenSet],
) -> None:
    graph, client, summarizer, golden = replay
    outcome = run_replay(graph, golden, client, summarizer)
    assert not outcome.missing, "\n".join(outcome.missing)
    # Every cached summary was consumed by the replay and nothing was consumed without
    # one: a row the golden set never reads (or a consumed question with no row) is
    # capture drift and must fail here, not only when a rewrite window happens to need it.
    assert outcome.consumed_questions == summarizer.known_questions
    assert not outcome.first_turn_failures, "\n".join(outcome.first_turn_failures)


@pytest.mark.eval
def test_followup_turns_match_the_expectations_at_the_floor(
    replay: tuple[Any, ReplayChatClient, CachedSummarizer, ChatGoldenSet],
) -> None:
    graph, client, summarizer, golden = replay
    outcome = run_replay(graph, golden, client, summarizer)
    assert not outcome.missing, "\n".join(outcome.missing)
    assert outcome.consumed_questions == summarizer.known_questions
    # The floor's own denominator: the golden set must keep its follow-up expectations,
    # or the accuracy below would be a vacuous 1.0.
    assert outcome.followup_total >= 10, outcome.followup_total
    floor = read_thresholds(THRESHOLDS).followup_accuracy
    accuracy = outcome.followup_hits / outcome.followup_total if outcome.followup_total else 1.0
    assert accuracy >= floor, "\n".join(outcome.failures)


@pytest.mark.eval
def test_followup_refusals_are_contained(
    replay: tuple[Any, ReplayChatClient, CachedSummarizer, ChatGoldenSet],
) -> None:
    graph, client, summarizer, golden = replay
    outcome = run_replay(graph, golden, client, summarizer)
    assert not outcome.missing, "\n".join(outcome.missing)
    assert outcome.consumed_questions == summarizer.known_questions
    # The refusal floor's denominator: the decision-request and injection follow-ups
    # must both stay in the golden set.
    assert outcome.refusal_total >= 2, outcome.refusal_total
    floor = read_thresholds(THRESHOLDS).followup_refusal_recall
    recall = outcome.refusal_hits / outcome.refusal_total if outcome.refusal_total else 1.0
    assert recall >= floor, "\n".join(outcome.failures)
