"""POST /ask-rule: the answered path, the refused path, upstream failures and the
missing-index case, using a stubbed use case plus one wiring test with real adapters."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndis.api import dependencies
from ndis.api.app import ask_rule, create_app
from ndis.api.dependencies import (
    EMBEDDING_CACHE_ENV,
    RULE_INDEX_PATH_ENV,
    embedding_cache_for,
    question_embedding_client,
    question_llm_client,
    rule_embedding_cache,
    rule_index_database_path,
    rule_store_for,
)
from ndis.application.extraction import LlmError
from ndis.application.pricing import AskRule, RuleAnswer
from ndis.domain.pricing import STRATEGY_FIXED, PricingChunk

ANSWER = RuleAnswer(
    question="Can a provider charge a cancellation fee?",
    answer="Yes, up to 100 per cent of the price with less than 5 business days notice.",
    citations=((12, "a cancellation fee of 100 per cent applies"),),
    refused=False,
    reason=None,
    top_similarity=0.82,
)
REFUSED = RuleAnswer(
    question="What is the capital of France?",
    answer=None,
    citations=(),
    refused=True,
    reason="below_similarity_threshold",
    top_similarity=0.18,
)


class StubAskRule:
    """Stands in for the use case at the dependency boundary."""

    def __init__(self, result: RuleAnswer, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.questions: list[str] = []

    def __call__(self, question: str) -> RuleAnswer:
        self.questions.append(question)
        if self.fail:
            raise LlmError("endpoint down")
        return self.result


@pytest.fixture
def client_factory() -> Callable[[StubAskRule], TestClient]:
    def make(stub: StubAskRule) -> TestClient:
        app = create_app()
        app.dependency_overrides[dependencies.ask_rule_use_case] = lambda: stub
        return TestClient(app)

    return make


def test_ask_rule_returns_answer_with_citations(
    client_factory: Callable[[StubAskRule], TestClient],
) -> None:
    stub = StubAskRule(ANSWER)
    response = client_factory(stub).post("/ask-rule", json={"question": ANSWER.question})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "question": ANSWER.question,
        "answer": ANSWER.answer,
        "citations": [{"page": 12, "quote": "a cancellation fee of 100 per cent applies"}],
        "refused": False,
        "reason": None,
        "top_similarity": 0.82,
    }
    assert stub.questions == [ANSWER.question]


def test_ask_rule_returns_a_refusal_for_off_topic_questions(
    client_factory: Callable[[StubAskRule], TestClient],
) -> None:
    stub = StubAskRule(REFUSED)
    response = client_factory(stub).post("/ask-rule", json={"question": REFUSED.question})
    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is True
    assert body["answer"] is None
    assert body["citations"] == []
    assert body["reason"] == "below_similarity_threshold"


def test_ask_rule_rejects_a_blank_question(
    client_factory: Callable[[StubAskRule], TestClient],
) -> None:
    stub = StubAskRule(ANSWER)
    response = client_factory(stub).post("/ask-rule", json={"question": "  "})
    assert response.status_code == 422


def test_ask_rule_translates_upstream_failures_to_502(
    client_factory: Callable[[StubAskRule], TestClient],
) -> None:
    stub = StubAskRule(ANSWER, fail=True)
    response = client_factory(stub).post("/ask-rule", json={"question": "anything"})
    assert response.status_code == 502
    assert "endpoint down" in response.json()["detail"]


def test_ask_rule_returns_503_when_the_index_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(RULE_INDEX_PATH_ENV, str(tmp_path / "absent.db"))
    with TestClient(create_app()) as client:
        response = client.post("/ask-rule", json={"question": "anything"})
    assert response.status_code == 503
    assert "not built" in response.json()["detail"]


def test_ask_rule_route_is_registered_by_name() -> None:
    app = create_app()
    names = {route.endpoint.__name__ for route in app.routes if hasattr(route, "endpoint")}
    assert ask_rule.__name__ in names


def test_rule_dependency_helpers_resolve_and_cache_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    index = tmp_path / "index.db"
    monkeypatch.setenv(RULE_INDEX_PATH_ENV, str(index))
    assert rule_index_database_path() == index
    store = rule_store_for(index)
    assert rule_store_for(index) is store  # one store per file for the process lifetime
    cache_path = tmp_path / "cache.db"
    monkeypatch.setenv(EMBEDDING_CACHE_ENV, str(cache_path))
    cache = rule_embedding_cache()
    assert rule_embedding_cache() is cache
    assert embedding_cache_for(cache_path) is cache
    cache.close()


def test_ask_rule_dependency_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The real dependency builds an AskRule over the store at ``RULE_INDEX_PATH`` with the
    configured strategy, threshold and top-k; embedding and LLM clients are faked out."""
    from ndis.adapters.rule_vector_store import SqliteVecRuleStore

    monkeypatch.delenv("RULE_CHUNK_STRATEGY", raising=False)
    monkeypatch.setenv("RULE_CHUNK_STRATEGY", STRATEGY_FIXED)
    index = tmp_path / "index.db"
    store = SqliteVecRuleStore(index)
    store.replace_strategy(
        STRATEGY_FIXED, (PricingChunk(STRATEGY_FIXED, 1, "text"),), ((1.0, 0.0),)
    )
    store.close()
    monkeypatch.setenv(RULE_INDEX_PATH_ENV, str(index))
    monkeypatch.setenv(EMBEDDING_CACHE_ENV, str(tmp_path / "cache.db"))
    monkeypatch.setattr(dependencies, question_embedding_client.__name__, lambda: StubEmbedder())
    monkeypatch.setattr(dependencies, question_llm_client.__name__, lambda: StubCompleter())
    use_case = dependencies.ask_rule_use_case(dependencies.rule_store())
    assert isinstance(use_case, AskRule)
    assert use_case._strategy == STRATEGY_FIXED
    assert use_case._threshold == dependencies.rule_similarity_threshold()
    assert use_case._top_k == dependencies.rule_top_k()


class StubEmbedder:
    @property
    def budget(self) -> object:  # pragma: no cover - wiring test never reads it
        return None

    def embed(self, texts: list[str]) -> object:  # pragma: no cover - not called here
        raise AssertionError("not called in the wiring test")


class StubCompleter:  # pragma: no cover - not called in the wiring test
    @property
    def budget(self) -> object:
        return None

    def complete(self, request: object) -> object:
        raise AssertionError("not called in the wiring test")
