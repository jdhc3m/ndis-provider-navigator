"""POST /chat and GET /chat/{thread_id} over a real chat graph (fake model, temp
repositories): the new-thread and follow-up answers, the restored conversation, and the
three thread-level rejections (malformed id, unknown thread, full thread)."""

import threading
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ndis.adapters.graphs.chat_graph import build_chat_graph
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.api import dependencies
from ndis.api.app import _thread_config, chat, chat_thread, create_app
from ndis.api.schemas import ChatIn
from ndis.application.ask import AskDeps, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import MAX_TURNS_PER_THREAD, ChatDeps
from ndis.application.extraction import LlmError, LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from tests.conftest import (
    SEED_PROVIDERS,
    SEED_SUPPORT_ITEMS,
    ChatFakeClient,
    ChatFakeEvidence,
    LocalityDirectoryStub,
    chat_intent_payload,
)

FIRST_QUESTION = "providers in postcode 4101"
NARROWED_QUESTION = "approved providers in postcode 4101"
FIRST_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101"}')
NARROWED_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101", "status": "Approved"}')


class FailingClient:
    """A fake client whose endpoint is down; the API must answer 502."""

    def complete(self, request: LlmRequest) -> LlmResult:
        del request
        raise LlmError("endpoint down")

    @property
    def budget(self) -> object:
        return None


@pytest.fixture
def repos(tmp_path: Path) -> tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository]:
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    providers = SqlAlchemyProviderRepository(engine)
    providers.replace_all(SEED_PROVIDERS, [])
    catalogue = SqlAlchemySupportItemRepository(engine)
    catalogue.replace_all(SEED_SUPPORT_ITEMS)
    return providers, catalogue


def make_api(
    providers: SqlAlchemyProviderRepository,
    catalogue: SqlAlchemySupportItemRepository,
    rewrite_payload: str,
    intent_payloads: Mapping[str, str],
) -> tuple[TestClient, ChatFakeClient]:
    """A TestClient whose /chat endpoints run a real chat graph over the temp
    repositories, with the fake model client and its own fresh checkpointer."""
    fake = ChatFakeClient(rewrite_payload, intent_payloads)
    deps = ChatDeps(
        client=fake,
        ask_deps=AskDeps(
            client=fake,
            providers=ListProviders(providers),
            catalogue=GetPriceLimit(catalogue),
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=ChatFakeEvidence(),
            locality_resolver=LocalityResolver(LocalityDirectoryStub(())),
            service_search=SearchSupportItems(catalogue),
        ),
        answer_to_json=lambda answer: _answer_json(answer),
    )
    saver = InMemorySaver()
    app = create_app()
    app.dependency_overrides[dependencies.chat_graph] = lambda: build_chat_graph(
        deps, checkpointer=saver
    )
    app.dependency_overrides[dependencies.chat_checkpointer] = lambda: saver
    return TestClient(app), fake


def _answer_json(answer: object) -> dict:
    from ndis.api.schemas import AskOut

    return AskOut.from_result(answer).model_dump(mode="json")  # type: ignore[arg-type]


def test_a_new_thread_is_answered_with_a_server_issued_uuid(repos) -> None:
    providers, catalogue = repos
    client, fake = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    response = client.post("/chat", json={"message": FIRST_QUESTION})
    assert response.status_code == 200
    body = response.json()
    uuid.UUID(body["thread_id"])  # the server created it, and it is a UUID
    assert body["standalone_question"] == FIRST_QUESTION
    assert body["rewritten"] is False and body["rewrite_reason"] is None
    assert body["answer"]["question"] == FIRST_QUESTION
    assert body["answer"]["refused"] is False
    assert body["answer"]["provider_total"] == 2
    assert [step["kind"] for step in body["answer"]["steps"]] == [
        "intent_parse",
        "provider_filter",
        "evidence",
        "guard",
    ]
    assert len(fake.requests) == 1  # one model call: the intent parse, no rewrite


def test_a_follow_up_on_the_same_thread_is_rewritten_and_narrows(repos) -> None:
    providers, catalogue = repos
    client, fake = make_api(
        providers,
        catalogue,
        f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
        {FIRST_QUESTION: FIRST_INTENT, NARROWED_QUESTION: NARROWED_INTENT},
    )
    thread_id = client.post("/chat", json={"message": FIRST_QUESTION}).json()["thread_id"]
    response = client.post(
        "/chat", json={"message": "only the approved ones", "thread_id": thread_id}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == thread_id
    assert body["standalone_question"] == NARROWED_QUESTION
    assert body["rewritten"] is True
    assert body["answer"]["provider_total"] == 1  # Delta Revoked is gone: narrowing ran
    assert len(fake.requests) == 3  # the first turn's parse + rewrite + the follow-up's


def test_get_returns_the_stored_turns_in_order(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(
        providers,
        catalogue,
        f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
        {FIRST_QUESTION: FIRST_INTENT, NARROWED_QUESTION: NARROWED_INTENT},
    )
    thread_id = client.post("/chat", json={"message": FIRST_QUESTION}).json()["thread_id"]
    client.post("/chat", json={"message": "only the approved ones", "thread_id": thread_id})
    response = client.get(f"/chat/{thread_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == thread_id
    assert [turn["message"] for turn in body["turns"]] == [
        FIRST_QUESTION,
        "only the approved ones",
    ]
    assert [turn["standalone_question"] for turn in body["turns"]] == [
        FIRST_QUESTION,
        NARROWED_QUESTION,
    ]
    assert body["turns"][0]["rewritten"] is False and body["turns"][1]["rewritten"] is True
    assert body["turns"][1]["answer"]["provider_total"] == 1


def test_the_chat_routes_are_registered_by_name() -> None:
    app = create_app()
    names = {route.endpoint.__name__ for route in app.routes if hasattr(route, "endpoint")}
    assert chat.__name__ in names
    assert chat_thread.__name__ in names


def test_thread_config_validates_the_id_builds_the_config_and_canonicalises() -> None:
    thread_id = str(uuid.uuid4())
    assert _thread_config(thread_id) == {"configurable": {"thread_id": thread_id}}
    # Any valid spelling addresses the same stored thread: the canonical form is the key.
    assert _thread_config(thread_id.upper()) == {"configurable": {"thread_id": thread_id}}
    with pytest.raises(Exception) as excinfo:
        _thread_config("not-a-uuid")
    assert getattr(excinfo.value, "status_code", None) == 422


def test_an_uppercase_spelled_thread_id_addresses_the_same_thread(repos) -> None:
    """The issued id is canonical; a client sending the same UUID uppercased still
    reaches the same stored thread instead of a 404."""
    providers, catalogue = repos
    client, _ = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    thread_id = client.post("/chat", json={"message": FIRST_QUESTION}).json()["thread_id"]
    response = client.post(
        "/chat", json={"message": FIRST_QUESTION, "thread_id": thread_id.upper()}
    )
    assert response.status_code == 200
    assert response.json()["thread_id"] == thread_id  # the answer carries the canonical id
    restored = client.get(f"/chat/{thread_id.upper()}")
    assert restored.status_code == 200  # the same thread, not a 404
    assert len(restored.json()["turns"]) == 2


def test_the_thread_id_validator_rejects_a_non_uuid() -> None:
    # The raw validator stays reachable on the model class: call it directly.
    with pytest.raises(ValueError):
        ChatIn._thread_id_must_be_a_uuid("abc")  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        ChatIn.model_validate({"message": "hello there", "thread_id": "abc"})


def test_the_answer_converter_produces_the_ask_shape(
    seeded_repository: SqlAlchemyProviderRepository,
) -> None:
    from ndis.api.dependencies import chat_answer_json
    from ndis.application.ask import CombinedAnswer
    from ndis.domain.intent import Intent, ProviderFilters

    answer = CombinedAnswer(
        question=FIRST_QUESTION,
        refused=False,
        reason=None,
        explanation=None,
        intent=Intent(provider_filters=ProviderFilters(postcode="4101")),
        provider_total=1,
    )
    document = chat_answer_json(answer)
    assert document["question"] == FIRST_QUESTION
    assert document["intent"]["provider_filters"]["postcode"] == "4101"


def test_a_malformed_thread_id_is_a_422(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    response = client.post("/chat", json={"message": FIRST_QUESTION, "thread_id": "abc"})
    assert response.status_code == 422
    assert client.get("/chat/not-a-uuid").status_code == 422


def test_an_unknown_thread_is_a_404_with_the_machine_reason(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    unknown = str(uuid.uuid4())
    response = client.post("/chat", json={"message": FIRST_QUESTION, "thread_id": unknown})
    assert response.status_code == 404
    assert response.json()["detail"]["reason"] == "thread_not_found"
    assert client.get(f"/chat/{unknown}").json()["detail"]["reason"] == "thread_not_found"
    assert client.get(f"/chat/{unknown}").status_code == 404


def test_a_full_thread_is_a_409_and_still_restores_its_turns(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(
        providers,
        catalogue,
        f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
        {FIRST_QUESTION: FIRST_INTENT, NARROWED_QUESTION: NARROWED_INTENT},
    )
    thread_id = client.post("/chat", json={"message": FIRST_QUESTION}).json()["thread_id"]
    for _ in range(MAX_TURNS_PER_THREAD - 1):
        response = client.post(
            "/chat", json={"message": "only the approved ones", "thread_id": thread_id}
        )
        assert response.status_code == 200
    response = client.post(
        "/chat", json={"message": "only the approved ones", "thread_id": thread_id}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "thread_full"
    restored = client.get(f"/chat/{thread_id}").json()
    assert len(restored["turns"]) == MAX_TURNS_PER_THREAD


def test_an_upstream_model_failure_is_a_502(repos, monkeypatch: pytest.MonkeyPatch) -> None:
    providers, catalogue = repos
    deps = ChatDeps(
        client=FailingClient(),  # type: ignore[arg-type]
        ask_deps=AskDeps(
            client=FailingClient(),  # type: ignore[arg-type]
            providers=ListProviders(providers),
            catalogue=GetPriceLimit(catalogue),
            rule_answerer=MissingIndexRuleAnswerer(),
            evidence=ChatFakeEvidence(),
            locality_resolver=LocalityResolver(LocalityDirectoryStub(())),
            service_search=SearchSupportItems(catalogue),
        ),
        answer_to_json=lambda answer: _answer_json(answer),
    )
    app = create_app()
    app.dependency_overrides[dependencies.chat_graph] = lambda: build_chat_graph(
        deps, checkpointer=InMemorySaver()
    )
    with TestClient(app) as client:
        response = client.post("/chat", json={"message": FIRST_QUESTION})
    assert response.status_code == 502
    assert "endpoint down" in response.json()["detail"]


def test_the_real_dependency_wiring_builds_the_chat_graph(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The composition root wires the chat graph over the repositories with the shared
    checkpointer, and the answer converter produces the API's own JSON shape."""
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    repository = SqlAlchemyProviderRepository(engine)
    repository.replace_all(SEED_PROVIDERS, [])
    monkeypatch.setenv(dependencies.DB_PATH_ENV, str(tmp_path / "ndis.db"))
    monkeypatch.setenv(dependencies.EXTRACTION_DIR_ENV, str(tmp_path / "extraction"))
    monkeypatch.setenv(dependencies.PAGES_DIR_ENV, str(tmp_path / "pages"))
    monkeypatch.setenv(dependencies.RULE_INDEX_PATH_ENV, str(tmp_path / "absent-index.db"))
    monkeypatch.setattr(dependencies, "question_llm_client", lambda: _UnusedClient())
    deps = dependencies.chat_deps(
        dependencies.list_providers_use_case(
            dependencies.provider_repository(), dependencies.postcode_centroids()
        ),
        dependencies.get_price_limit_use_case(dependencies.support_item_repository()),
        dependencies.claim_evidence_source(),
        dependencies.locality_resolver(),
        dependencies.search_support_items_use_case(dependencies.support_item_repository()),
    )
    graph = dependencies.chat_graph(deps)
    assert graph.checkpointer is dependencies.chat_checkpointer()


class _UnusedClient:
    @property
    def budget(self) -> object:
        return None

    def complete(self, request: object) -> object:
        raise AssertionError("wiring test never completes a call")


def _race_statuses(racers: list[TestClient], thread_id: str) -> list[int]:
    """Two POSTs on one thread, started together: the status codes they answered with."""
    barrier = threading.Barrier(len(racers))

    def post_once(racer: TestClient) -> int:
        barrier.wait()
        return racer.post(
            "/chat", json={"message": "only the approved ones", "thread_id": thread_id}
        ).status_code

    with ThreadPoolExecutor(max_workers=len(racers)) as pool:
        return sorted(pool.map(post_once, racers))


def _seed_turns(client: TestClient, count: int) -> str:
    """One thread at ``count`` turns; every turn is one the fake model answers."""
    thread_id: str | None = None
    for index in range(count):
        message = FIRST_QUESTION if index == 0 else "only the approved ones"
        response = client.post("/chat", json={"message": message, "thread_id": thread_id})
        assert response.status_code == 200, response.status_code
        thread_id = response.json()["thread_id"]
    assert thread_id is not None
    return thread_id


def test_two_concurrent_posts_cannot_push_a_thread_past_the_cap(repos) -> None:
    """Regression (review round 1): the 409 check and the invoke were a check-then-act
    pair with no lock, and sync handlers run in FastAPI's threadpool, so two POSTs on a
    49-turn thread could both pass the check and both append — 51 persisted turns. The
    pair must be serial: one 200 (turn 50), one 409, and 50 stored turns."""
    providers, catalogue = repos
    intents = {FIRST_QUESTION: FIRST_INTENT, NARROWED_QUESTION: NARROWED_INTENT}
    for _round in range(3):
        client, _ = make_api(
            providers,
            catalogue,
            f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
            intents,
        )
        thread_id = _seed_turns(client, MAX_TURNS_PER_THREAD - 1)
        racers = [TestClient(client.app), TestClient(client.app)]
        statuses = _race_statuses(racers, thread_id)
        # One turn lands, the other is refused: the cap held under concurrency.
        assert statuses == [200, 409], statuses
        turns = client.get(f"/chat/{thread_id}").json()["turns"]
        assert len(turns) == MAX_TURNS_PER_THREAD, len(turns)
