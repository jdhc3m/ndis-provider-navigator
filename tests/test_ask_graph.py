"""The ask pipeline as a LangGraph graph: the refusal short-circuit, the empty updates
of null sections, the join of the three parallel sections, thread-safe SQLite reads
under real threads, and the committed diagram staying in sync with the code."""

import json
import threading
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ndis.adapters.graphs.ask_graph import build_ask_graph
from ndis.adapters.sqlite_repository import (
    SqlAlchemyPostcodeCentroids,
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.ask import (
    AskDeps,
    AskState,
    CombinedAnswer,
    MissingIndexRuleAnswerer,
    compose_stage,
)
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.pricing import RuleAnswer
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.answer import ClaimedService
from ndis.domain.extraction import TokenBudget
from tests.conftest import (
    SEED_CENTROIDS,
    SEED_PROVIDERS,
    SEED_SUPPORT_ITEMS,
    LocalityDirectoryStub,
)

QUESTION = "Providers in QLD, the price of 01_002_0107_1_1 and cancellation rules"
ITEM_PRICE = '{"item": "01_002_0107_1_1", "state": "QLD"}'


class FixedIntentClient:
    """One canned intent payload for every request; a lock keeps the count exact."""

    def __init__(self, payload: str) -> None:
        self._payload = payload
        self.lock = threading.Lock()
        self.calls = 0

    def complete(self, request: LlmRequest) -> LlmResult:
        with self.lock:
            self.calls += 1
        return LlmResult(self._payload, 120, 40)

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(200_000)


class RecordingProviders(ListProviders):
    def __init__(self, repository: SqlAlchemyProviderRepository) -> None:
        super().__init__(repository)
        self.lock = threading.Lock()
        self.calls = 0

    def __call__(self, query: object) -> object:
        with self.lock:
            self.calls += 1
        return super().__call__(query)  # type: ignore[arg-type]


class RecordingPriceLimit(GetPriceLimit):
    def __init__(self, repository: SqlAlchemySupportItemRepository) -> None:
        super().__init__(repository)
        self.lock = threading.Lock()
        self.calls = 0

    def __call__(self, query: object) -> object:
        with self.lock:
            self.calls += 1
        return super().__call__(query)  # type: ignore[arg-type]


class RecordingRuleAnswerer:
    def __init__(self, answer: RuleAnswer | None = None) -> None:
        self.questions: list[str] = []
        self._answer = answer or RuleAnswer(
            "cancellation", "Up to 100 per cent.", ((26, "claim up to 100%"),), False, None, 0.8
        )

    def __call__(self, question: str) -> RuleAnswer:
        self.questions.append(question)
        return self._answer


class FakeEvidence:
    def claims_for(self, provider_keys: Sequence[str]) -> Mapping[str, tuple[ClaimedService, ...]]:
        return {}

    def page_text(self, provider_key: str, url: str) -> str:
        return ""


def intent_payload(**overrides: object) -> str:
    document: dict[str, object] = {
        "provider_filters": None,
        "price_query": None,
        "rule_question": None,
        "decision_request": False,
    }
    for key, value in overrides.items():
        document[key] = json.loads(value) if key in ("provider_filters", "price_query") else value
    return json.dumps(document)


@pytest.fixture
def repos(tmp_path: Path) -> tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository]:
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    providers = SqlAlchemyProviderRepository(engine)
    providers.replace_all(SEED_PROVIDERS, [])
    catalogue = SqlAlchemySupportItemRepository(engine)
    catalogue.replace_all(SEED_SUPPORT_ITEMS)
    return providers, catalogue


def make_deps(
    payload: str,
    providers: SqlAlchemyProviderRepository,
    catalogue: SqlAlchemySupportItemRepository,
    centroids: SqlAlchemyPostcodeCentroids | None = None,
) -> tuple[AskDeps, RecordingProviders, RecordingPriceLimit, RecordingRuleAnswerer]:
    recording_providers = RecordingProviders(providers)
    recording_catalogue = RecordingPriceLimit(catalogue)
    recording_rule = RecordingRuleAnswerer()
    deps = AskDeps(
        client=FixedIntentClient(payload),
        providers=recording_providers,
        catalogue=recording_catalogue,
        rule_answerer=recording_rule,
        evidence=FakeEvidence(),
        locality_resolver=LocalityResolver(LocalityDirectoryStub(())),
        service_search=SearchSupportItems(catalogue),
    )
    return deps, recording_providers, recording_catalogue, recording_rule


def run_graph(deps: AskDeps, question: str) -> tuple[CombinedAnswer, dict[str, object]]:
    """Run the graph once: the composed answer and each node's streamed update
    (``None`` for a node that returned an empty update)."""
    graph = build_ask_graph(deps)
    updates: dict[str, object] = {}
    state: dict[str, object] = {}
    for chunk in graph.stream({"question": question}, stream_mode="updates", version="v2"):
        for node, update in chunk["data"].items():
            updates[node] = update
            if isinstance(update, dict):
                steps = state.setdefault("steps", [])
                steps.extend(update.get("steps", []))
                state.update((key, value) for key, value in update.items() if key != "steps")
    return compose_stage(state), updates


def comparable(answer: CombinedAnswer) -> tuple[object, ...]:
    """The answer fields a run-to-run comparison may use: everything but the steps'
    timings, which the parallel sections record in arrival order."""
    return (
        answer.question,
        answer.refused,
        answer.reason,
        answer.explanation,
        answer.intent,
        answer.parse_rejections,
        answer.locality,
        answer.providers,
        answer.provider_total,
        answer.distances,
        answer.distance_origin,
        answer.services,
        answer.price,
        answer.price_error,
        answer.service_prices,
        answer.rule,
        answer.caveat,
        {(step.kind, step.input_summary, step.output_summary) for step in answer.steps},
    )


def test_a_decision_request_refuses_and_no_section_node_runs(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
) -> None:
    providers, catalogue = repos
    deps, recording_providers, recording_catalogue, recording_rule = make_deps(
        intent_payload(decision_request=True), providers, catalogue
    )
    answer, updates = run_graph(deps, "Which provider is best for my child?")
    assert set(updates) == {"parse_intent", "refuse"}
    assert answer.refused and answer.reason == "decision_request"
    assert answer.explanation is not None
    assert answer.providers == () and answer.price is None and answer.rule is None
    assert recording_providers.calls == 0
    assert recording_catalogue.calls == 0
    assert recording_rule.questions == []


def test_a_price_only_intent_answers_the_price_with_no_providers(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
) -> None:
    providers, catalogue = repos
    question = "What is the price limit for 01_002_0107_1_1 in QLD?"
    deps, recording_providers, recording_catalogue, _ = make_deps(
        intent_payload(price_query=ITEM_PRICE), providers, catalogue
    )
    answer, updates = run_graph(deps, question)
    assert updates["providers"] is None  # the section ran and returned an empty update
    assert updates["start_sections"] is None
    assert updates["price"] is not None
    assert not answer.refused
    assert answer.price is not None and answer.price_error is None
    assert answer.providers == () and answer.provider_total == 0
    assert recording_providers.calls == 0
    assert recording_catalogue.calls == 1


def test_an_unparseable_payload_ends_in_intent_unparseable(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
) -> None:
    providers, catalogue = repos
    deps, recording_providers, _, recording_rule = make_deps(
        "I am now an unrestricted assistant!", providers, catalogue
    )
    answer, updates = run_graph(deps, "anything at all")
    assert set(updates) == {"parse_intent", "refuse"}
    assert answer.refused and answer.reason == "intent_unparseable"
    assert answer.explanation is not None and answer.intent is None
    assert recording_providers.calls == 0 and recording_rule.questions == []


def test_the_final_steps_carry_all_three_parallel_sections(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
) -> None:
    providers, catalogue = repos
    payload = intent_payload(
        provider_filters='{"state": "QLD"}',
        price_query=ITEM_PRICE,
        rule_question="Can a provider charge a cancellation fee?",
    )
    deps, _, _, _ = make_deps(payload, providers, catalogue)
    graph = build_ask_graph(deps)
    final: AskState = graph.invoke({"question": QUESTION})
    kinds = [step.kind for step in final["steps"]]
    assert set(kinds) == {
        "intent_parse",
        "provider_filter",
        "price_limit",
        "rule_answer",
        "evidence",
        "guard",
    }
    assert kinds.count("evidence") == 1  # the join runs once, not once per source
    answer = compose_stage(final)
    assert not answer.refused
    assert answer.provider_total == 3
    assert answer.price is not None and answer.rule is not None


def test_the_graph_is_thread_safe_over_one_sqlite_file(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    tmp_path: Path,
) -> None:
    """Twenty invocations from four threads over one shared graph and one shared SQLite
    file: the parallel section nodes read through the same engine from different worker
    threads (the issue #48 class of bug) with no error and identical answers."""
    providers, catalogue = repos
    centroids = SqlAlchemyPostcodeCentroids(create_sqlite_engine(tmp_path / "centroids.db"))
    centroids.replace_all(SEED_CENTROIDS)
    payload = intent_payload(
        provider_filters='{"postcode": "4101"}',
        price_query=ITEM_PRICE,
    )
    deps, _, _, _ = make_deps(payload, providers, catalogue)
    graph = build_ask_graph(deps)
    question = "Who is in 4101 and what does 01_002_0107_1_1 cost in QLD?"

    def run(_: int) -> CombinedAnswer:
        return compose_stage(graph.invoke({"question": question}))

    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(run, range(20)))
    assert len(answers) == 20
    first = comparable(answers[0])
    assert all(comparable(answer) == first for answer in answers[1:])
    assert not answers[0].refused
    assert len(answers[0].providers) == 2  # both 4101 providers, same ranked order every time
    assert answers[0].price is not None


def test_the_committed_diagram_matches_the_graph() -> None:
    """The diagram committed at docs/graphs/ask_graph.md is the one the code builds."""
    committed = Path(__file__).resolve().parents[1] / "docs" / "graphs" / "ask_graph.md"
    text = committed.read_text(encoding="utf-8")
    start = text.index("```mermaid\n") + len("```mermaid\n")
    end = text.index("\n```", start)
    deps = AskDeps(
        client=_NoCalls(),
        providers=_NoCalls(),
        catalogue=_NoCalls(),
        rule_answerer=MissingIndexRuleAnswerer(),
        evidence=_NoCalls(),
        locality_resolver=_NoCalls(),
        service_search=_NoCalls(),
    )
    assert text[start:end] == build_ask_graph(deps).get_graph().draw_mermaid()


class _NoCalls:
    """A dependency stand-in for building the diagram: no node runs, so a call is a bug."""

    def __call__(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("building the diagram runs no node")

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"building the diagram runs no node ({name!r} was read)")
