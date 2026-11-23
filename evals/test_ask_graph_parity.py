"""Ticket 15 parity eval: the ask graph's final answer equals the ``CombinedAnswer`` the
straight-line ``Ask`` use case returns, field for field, for every case the golden eval
replays from ``evals/intent_cache.yaml``. The ``steps`` are compared as a set of
``(kind, input_summary, output_summary)`` because the graph runs the three sections in
parallel and timing and order may differ.

The run replays the captured payloads like ``evals/test_ask_golden.py``: the parse, the
guard and the deterministic execution run for real on both paths, and zero model calls
are made. Skips visibly while the register database or the capture is missing on this
machine. The database file comes from ``NDIS_DB_PATH``, defaulting to ``data/ndis.db``.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from langgraph.graph.state import CompiledStateGraph

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.golden_questions import read_intent_cache
from ndis.adapters.graphs.ask_graph import build_ask_graph
from ndis.adapters.sqlite_repository import (
    SqlAlchemyPostcodeCentroids,
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.ask import (
    Ask,
    AskDeps,
    MissingIndexRuleAnswerer,
    compose_stage,
)
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.extraction import TokenBudget

# Replays every golden case through both paths against the full register snapshot —
# minutes, not seconds. Deselected by the default run (see the ``slow`` marker in
# pyproject); the pre-push gate ``uv run pytest -m eval --no-cov`` and CI select it again.
pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "evals" / "intent_cache.yaml"
DB = Path(os.environ.get("NDIS_DB_PATH") or ROOT / "data" / "ndis.db")
EXTRACTION = ROOT / "data" / "private" / "extraction"
PAGES = ROOT / "data" / "private" / "pages"

FILTER_PAGE = 5

# Every ``CombinedAnswer`` field, compared field for field; only ``steps`` is excluded,
# because the graph records it in arrival order.
FIELDS = (
    "question",
    "refused",
    "reason",
    "explanation",
    "intent",
    "parse_rejections",
    "locality",
    "providers",
    "provider_total",
    "distances",
    "distance_origin",
    "services",
    "price",
    "price_error",
    "service_prices",
    "rule",
    "caveat",
)


class CachedIntentClient:
    """A fake ``LlmClient`` that answers with the captured payload for the question in
    the request's user prompt — keyed by question, not call order."""

    def __init__(self, payloads: Mapping[str, str]) -> None:
        self._payloads = dict(payloads)
        self.asked: list[str] = []

    def complete(self, request: LlmRequest) -> LlmResult:
        prefix = "Question: "
        start = request.user.index(prefix) + len(prefix)
        question = request.user[start : request.user.index("\n\n", start)]
        self.asked.append(question)
        return LlmResult(self._payloads[question], 0, 0)

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(0)


@pytest.fixture
def payloads() -> dict[str, str]:
    if not CACHE.is_file():
        pytest.skip(f"intent cache not captured yet: {CACHE} is missing")
    if not DB.is_file():
        pytest.skip(f"register database not loaded yet: {DB} is missing")
    return read_intent_cache(CACHE)


@pytest.fixture
def runners(payloads: dict[str, str]) -> tuple[Ask, CompiledStateGraph]:  # type: ignore[type-arg]
    engine = create_sqlite_engine(DB)
    evidence = ExtractionEvidenceSource(engine, EXTRACTION, PAGES)
    providers = ListProviders(SqlAlchemyProviderRepository(engine))
    catalogue = GetPriceLimit(SqlAlchemySupportItemRepository(engine))
    localities = LocalityResolver(SqlAlchemyPostcodeCentroids(engine))
    service_search = SearchSupportItems(SqlAlchemySupportItemRepository(engine))
    ask = Ask(
        CachedIntentClient(payloads),
        providers,
        catalogue,
        MissingIndexRuleAnswerer(),
        evidence,
        locality_resolver=localities,
        service_search=service_search,
        provider_limit=FILTER_PAGE,
    )
    graph = build_ask_graph(
        AskDeps(
            client=CachedIntentClient(payloads),
            providers=providers,
            catalogue=catalogue,
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=evidence,
            locality_resolver=localities,
            service_search=service_search,
            provider_limit=FILTER_PAGE,
        )
    )
    return ask, graph


@pytest.mark.eval
def test_the_graphs_final_answer_equals_the_straight_line_answer(
    runners: tuple[Ask, object],
    payloads: dict[str, str],
) -> None:
    ask, graph = runners
    failures: list[str] = []
    for question in payloads:
        straight = ask(question)
        through_graph = compose_stage(graph.invoke({"question": question}))
        for field in FIELDS:
            if getattr(straight, field) != getattr(through_graph, field):
                failures.append(f"{question}: {field} differs")
        straight_steps = {
            (step.kind, step.input_summary, step.output_summary) for step in straight.steps
        }
        graph_steps = {
            (step.kind, step.input_summary, step.output_summary) for step in through_graph.steps
        }
        if straight_steps != graph_steps:
            failures.append(
                f"{question}: steps differ\n"
                f"  straight only: {sorted(straight_steps - graph_steps)}\n"
                f"  graph only: {sorted(graph_steps - straight_steps)}"
            )
    assert not failures, f"{len(failures)} of {len(payloads)} cases differ:\n" + "\n".join(failures)
