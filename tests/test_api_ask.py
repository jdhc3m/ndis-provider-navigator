"""POST /ask: the answered path, the refusal path, upstream failures and the wiring,
using a stubbed use case plus one wiring test with real adapters."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    create_sqlite_engine,
)
from ndis.api import dependencies
from ndis.api.app import ask, create_app
from ndis.api.dependencies import (
    EXTRACTION_DIR_ENV,
    PAGES_DIR_ENV,
    RULE_INDEX_PATH_ENV,
    claim_evidence_source,
    rule_answerer_for,
    rule_store_maybe,
)
from ndis.application.ask import Ask, CombinedAnswer
from ndis.application.extraction import LlmError
from ndis.domain.answer import CAVEAT_SERVICES, ServiceMention
from ndis.domain.intent import Intent, ProviderFilters
from ndis.domain.pricing import REFUSAL_NO_CHUNKS
from tests.conftest import SEED_PROVIDERS

DECISION = CombinedAnswer(
    question="Which provider is best for my child?",
    refused=True,
    reason="decision_request",
    explanation="This system does not make recommendations and does not make funding decisions.",
    intent=Intent(decision_request=True),
)
ANSWERED = CombinedAnswer(
    question="providers in NSW",
    refused=False,
    reason=None,
    explanation=None,
    intent=Intent(provider_filters=ProviderFilters(state="NSW")),
    providers=(),
    provider_total=1,
    services={},
    price=None,
    price_error=None,
    rule=None,
    caveat=None,
)


class StubAsk:
    def __init__(self, result: CombinedAnswer, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.questions: list[str] = []

    def __call__(self, question: str) -> CombinedAnswer:
        self.questions.append(question)
        if self.fail:
            raise LlmError("endpoint down")
        return self.result


@pytest.fixture
def client_factory() -> Callable[[StubAsk], TestClient]:
    def make(stub: StubAsk) -> TestClient:
        app = create_app()
        app.dependency_overrides[dependencies.ask_use_case] = lambda: stub
        return TestClient(app)

    return make


def test_ask_returns_the_combined_answer(
    client_factory: Callable[[StubAsk], TestClient],
) -> None:
    stub = StubAsk(ANSWERED)
    response = client_factory(stub).post("/ask", json={"question": "providers in NSW"})
    assert response.status_code == 200
    assert response.json() == {
        "question": "providers in NSW",
        "refused": False,
        "reason": None,
        "explanation": None,
        "intent": {
            "provider_filters": {
                "state": "NSW",
                "postcode": None,
                "group": None,
                "status": None,
                "brisbane_area": None,
            },
            "price_query": None,
            "rule_question": None,
            "decision_request": False,
            "parse_rejections": [],
        },
        "providers": [],
        "provider_total": 1,
        "price": None,
        "price_error": None,
        "rule": None,
        "caveat": None,
    }
    assert stub.questions == ["providers in NSW"]


def test_ask_returns_a_refusal_with_the_explanation(
    client_factory: Callable[[StubAsk], TestClient],
) -> None:
    stub = StubAsk(DECISION)
    response = client_factory(stub).post("/ask", json={"question": DECISION.question})
    assert response.status_code == 200
    body = response.json()
    assert body["refused"] is True
    assert body["reason"] == "decision_request"
    assert "does not make recommendations" in body["explanation"]
    assert body["providers"] == []
    assert body["intent"]["decision_request"] is True


def test_ask_includes_services_and_the_exact_caveat(
    client_factory: Callable[[StubAsk], TestClient],
) -> None:
    mention = ServiceMention("speech_pathology", "https://x.example/", "a" * 30, "2026-11-07")
    answered = CombinedAnswer(
        question="speech pathology in NSW",
        refused=False,
        reason=None,
        explanation=None,
        intent=Intent(provider_filters=ProviderFilters(state="NSW")),
        provider_total=1,
        services={"10000000003": (mention,)},
        caveat=CAVEAT_SERVICES,
    )
    stub = StubAsk(answered)
    response = client_factory(stub).post("/ask", json={"question": "speech pathology in NSW"})
    body = response.json()
    assert body["caveat"] == (
        "the register does not record profession; services come from the provider's website"
    )
    assert body["providers"] == []
    assert response.status_code == 200


def test_ask_rejects_an_out_of_range_question(
    client_factory: Callable[[StubAsk], TestClient],
) -> None:
    stub = StubAsk(ANSWERED)
    response = client_factory(stub).post("/ask", json={"question": "  "})
    assert response.status_code == 422


def test_ask_translates_upstream_failures_to_502(
    client_factory: Callable[[StubAsk], TestClient],
) -> None:
    stub = StubAsk(ANSWERED, fail=True)
    response = client_factory(stub).post("/ask", json={"question": "anything"})
    assert response.status_code == 502
    assert "endpoint down" in response.json()["detail"]


def test_ask_route_is_registered_by_name() -> None:
    app = create_app()
    names = {route.endpoint.__name__ for route in app.routes if hasattr(route, "endpoint")}
    assert ask.__name__ in names


def test_ask_dependency_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The real dependency builds the Ask use case over the repositories, the evidence
    source and the refusing stand-in when no rule index exists; the LLM client is faked."""
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    repository = SqlAlchemyProviderRepository(engine)
    repository.replace_all(SEED_PROVIDERS, [])
    monkeypatch.setenv(dependencies.DB_PATH_ENV, str(tmp_path / "ndis.db"))
    monkeypatch.setenv(EXTRACTION_DIR_ENV, str(tmp_path / "extraction"))
    monkeypatch.setenv(PAGES_DIR_ENV, str(tmp_path / "pages"))
    monkeypatch.setenv(RULE_INDEX_PATH_ENV, str(tmp_path / "absent-index.db"))
    monkeypatch.setattr(dependencies, "question_llm_client", lambda: StubCompleter())
    use_case = dependencies.ask_use_case(
        dependencies.list_providers_use_case(dependencies.provider_repository()),
        dependencies.get_price_limit_use_case(dependencies.support_item_repository()),
        claim_evidence_source(),
    )
    assert isinstance(use_case, Ask)
    assert rule_store_maybe() is None
    answer = rule_answerer_for(None)("any rule question")
    assert answer.refused and answer.reason == REFUSAL_NO_CHUNKS


class StubCompleter:
    @property
    def budget(self) -> object:  # pragma: no cover - wiring test never reads it
        return None

    def complete(self, request: object) -> object:  # pragma: no cover - not called here
        raise AssertionError("not called in the wiring test")


def test_extraction_and_pages_dirs_come_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(EXTRACTION_DIR_ENV, raising=False)
    monkeypatch.delenv(PAGES_DIR_ENV, raising=False)
    assert dependencies.extraction_dir() == dependencies.DEFAULT_EXTRACTION_DIR
    assert dependencies.pages_dir() == dependencies.DEFAULT_PAGES_DIR
    monkeypatch.setenv(EXTRACTION_DIR_ENV, str(tmp_path / "extraction"))
    monkeypatch.setenv(PAGES_DIR_ENV, str(tmp_path / "pages"))
    assert dependencies.extraction_dir() == tmp_path / "extraction"
    assert dependencies.pages_dir() == tmp_path / "pages"


def test_intent_and_service_mention_schemas_convert_domain_values() -> None:
    from ndis.api.schemas import AskIntentOut, ServiceMentionOut
    from ndis.domain.intent import PriceQuery

    intent = Intent(
        provider_filters=ProviderFilters(state="QLD", postcode="4101"),
        price_query=PriceQuery("01_002_0107_1_1", "QLD", None),
    )
    intent_out = AskIntentOut.from_intent(intent, ("x",))
    assert intent_out.provider_filters is not None
    assert intent_out.provider_filters.state == "QLD"
    assert intent_out.price_query is not None
    assert intent_out.price_query.item == "01_002_0107_1_1"
    assert intent_out.parse_rejections == ["x"]
    mention_out = ServiceMentionOut.from_mention(
        ServiceMention("speech_pathology", "https://x.example/", "a" * 30, "2026-11-07")
    )
    assert mention_out.service == "Speech pathology"
    assert mention_out.fetched_at == "2026-11-07"
