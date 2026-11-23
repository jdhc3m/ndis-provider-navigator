"""POST /chat, its resume and GET /chat/{thread_id} over a real chat graph (fake model,
temp repositories): the new-thread and follow-up answers, the restored conversation, the
pause that asks which place the user meant, and the thread-level rejections (malformed
id, unknown thread, full thread, resume with nothing pending)."""

import threading
import uuid
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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
from ndis.api.app import (
    _chat_turn_outcome,
    _invoke_turn,
    _thread_config,
    chat,
    chat_resume,
    chat_thread,
    create_app,
)
from ndis.api.schemas import ChatIn
from ndis.application.ask import AskDeps, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import MAX_TURNS_PER_THREAD, ChatDeps
from ndis.application.extraction import LlmError, LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.locality import Locality
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
    *,
    localities: Sequence[Locality] = (),
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
            locality_resolver=LocalityResolver(LocalityDirectoryStub(localities)),
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
    assert chat_resume.__name__ in names
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


# --- The pause over the API (ticket 18) ------------------------------------------------------

# The real gazetteer spells Paddington twice: QLD 4064 and NSW 2021 (the fixture rows keep
# the CSV's uppercase spelling, as the loader stores them).
PADDINGTON_ROWS = [
    Locality(name="PADDINGTON", state="QLD", postcode="4064"),
    Locality(name="PADDINGTON", state="NSW", postcode="2021"),
]
AMBIGUOUS_QUESTION = "plan managers in Paddington"
AMBIGUOUS_INTENT = chat_intent_payload(
    provider_filters='{"locality": "Paddington", "group": "Plan Management"}'
)
EXPECTED_OPTIONS = [
    {"id": "nsw-2021", "label": "NSW 2021"},
    {"id": "qld-4064", "label": "QLD 4064"},
]


def ambiguous_api(
    providers: SqlAlchemyProviderRepository, catalogue: SqlAlchemySupportItemRepository
) -> tuple[TestClient, str]:
    """The API with the ambiguous gazetteer, and a thread paused on the Paddington
    question."""
    client, _ = make_api(
        providers,
        catalogue,
        "",
        {AMBIGUOUS_QUESTION: AMBIGUOUS_INTENT},
        localities=PADDINGTON_ROWS,
    )
    body = client.post("/chat", json={"message": AMBIGUOUS_QUESTION}).json()
    assert body["status"] == "needs_input"
    return client, body["thread_id"]


def test_an_ambiguous_locality_answers_needs_input_with_the_pending_options(repos) -> None:
    providers, catalogue = repos
    client, thread_id = ambiguous_api(providers, catalogue)
    body = client.post("/chat", json={"message": AMBIGUOUS_QUESTION, "thread_id": thread_id}).json()
    assert body["status"] == "needs_input"
    assert body["thread_id"] == thread_id
    assert body["answer"] is None
    assert body["standalone_question"] == AMBIGUOUS_QUESTION
    pending = body["pending"]
    assert pending["kind"] == "choose_locality"
    assert pending["question"] == 'Which "Paddington" do you mean?'
    assert pending["options"] == EXPECTED_OPTIONS
    # The paused turn is not stored: the thread still holds no turns.
    restored = client.get(f"/chat/{thread_id}").json()
    assert restored["turns"] == []


def test_get_returns_the_pending_question_so_a_reload_shows_it_again(repos) -> None:
    providers, catalogue = repos
    client, thread_id = ambiguous_api(providers, catalogue)
    restored = client.get(f"/chat/{thread_id}").json()
    assert restored["pending"] is not None
    assert restored["pending"]["kind"] == "choose_locality"
    assert restored["pending"]["options"] == EXPECTED_OPTIONS


def test_the_resume_completes_the_same_turn_with_the_chosen_place(repos) -> None:
    providers, catalogue = repos
    client, thread_id = ambiguous_api(providers, catalogue)
    response = client.post(f"/chat/{thread_id}/resume", json={"option_id": "qld-4064"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["pending"] is None
    assert body["standalone_question"] == AMBIGUOUS_QUESTION
    assert body["answer"]["refused"] is False
    locality = body["answer"]["locality"]
    assert (locality["outcome"], locality["state"], locality["postcode"]) == (
        "resolved",
        "QLD",
        "4064",
    )
    # The resumed turn is stored like any other, and nothing is pending any more.
    restored = client.get(f"/chat/{thread_id}").json()
    assert restored["pending"] is None
    assert [turn["message"] for turn in restored["turns"]] == [AMBIGUOUS_QUESTION]
    assert restored["turns"][0]["answer"]["locality"]["postcode"] == "4064"


def test_an_invalid_option_reasks_with_a_reason_and_a_valid_one_answers(repos) -> None:
    providers, catalogue = repos
    client, thread_id = ambiguous_api(providers, catalogue)
    reask = client.post(f"/chat/{thread_id}/resume", json={"option_id": "qld-9999"})
    assert reask.status_code == 200
    body = reask.json()
    assert body["status"] == "needs_input"
    assert body["answer"] is None
    assert body["pending"]["reason"] == "invalid_option"
    assert body["pending"]["options"] == EXPECTED_OPTIONS
    # The same options, asked again, until one of them is named.
    answered = client.post(f"/chat/{thread_id}/resume", json={"option_id": "nsw-2021"})
    assert answered.status_code == 200
    assert answered.json()["status"] == "answered"
    locality = answered.json()["answer"]["locality"]
    assert (locality["state"], locality["postcode"]) == ("NSW", "2021")


def test_resume_on_a_thread_with_nothing_pending_is_a_409(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    # No thread at all, and a thread whose last turn already completed.
    missing = str(uuid.uuid4())
    assert client.post(f"/chat/{missing}/resume", json={"option_id": "qld-4064"}).status_code == 409
    thread_id = client.post("/chat", json={"message": FIRST_QUESTION}).json()["thread_id"]
    response = client.post(f"/chat/{thread_id}/resume", json={"option_id": "qld-4064"})
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "nothing_pending"


def test_a_new_message_on_a_pending_thread_drops_the_question(repos) -> None:
    providers, catalogue = repos
    client, _ = make_api(
        providers,
        catalogue,
        "",
        {AMBIGUOUS_QUESTION: AMBIGUOUS_INTENT, FIRST_QUESTION: FIRST_INTENT},
        localities=PADDINGTON_ROWS,
    )
    thread_id = client.post("/chat", json={"message": AMBIGUOUS_QUESTION}).json()["thread_id"]
    response = client.post("/chat", json={"message": FIRST_QUESTION, "thread_id": thread_id})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["pending"] is None
    assert body["answer"]["provider_total"] == 2  # the postcode turn, answered normally
    restored = client.get(f"/chat/{thread_id}").json()
    assert restored["pending"] is None
    assert [turn["message"] for turn in restored["turns"]] == [FIRST_QUESTION]


def test_the_shared_outcome_helper_shapes_both_statuses(repos) -> None:
    """The helper both chat endpoints share: an answered turn shapes as before, a paused
    one returns the pending question with no answer."""
    from types import SimpleNamespace

    providers, catalogue = repos
    client, _ = make_api(providers, catalogue, "", {FIRST_QUESTION: FIRST_INTENT})
    body = client.post("/chat", json={"message": FIRST_QUESTION}).json()
    stored = client.get(f"/chat/{body['thread_id']}").json()["turns"][0]
    answered = _chat_turn_outcome(body["thread_id"], {"turns": [stored]}, stored)
    assert answered.status == "answered"
    assert answered.answer is not None and answered.pending is None
    assert answered.answer is not None and answered.answer.question == FIRST_QUESTION
    payload = {
        "kind": "choose_locality",
        "question": 'Which "Paddington" do you mean?',
        "options": [{"id": "qld-4064", "label": "QLD 4064"}],
        "reason": None,
    }
    paused = _chat_turn_outcome(
        body["thread_id"],
        {"__interrupt__": [SimpleNamespace(value=payload)]},
        {"message": AMBIGUOUS_QUESTION},
    )
    assert paused.status == "needs_input"
    assert paused.answer is None
    assert paused.pending is not None and paused.pending.options[0].id == "qld-4064"
    assert paused.standalone_question == AMBIGUOUS_QUESTION


def test_the_shared_invoke_helper_maps_model_failures_to_502() -> None:
    from fastapi import HTTPException

    class ExplodingGraph:
        def invoke(self, request: object, config: object) -> object:
            del request, config
            raise LlmError("endpoint down")

    with pytest.raises(HTTPException) as excinfo:
        _invoke_turn(ExplodingGraph(), {}, {"message": FIRST_QUESTION})
    assert excinfo.value.status_code == 502
    assert "endpoint down" in str(excinfo.value.detail)


class _GatedResumeGraph:
    """The real chat graph with the first resume's invoke gated mid-flight: it signals
    ``started`` and then waits for ``may_proceed``, so the turn is still pending while
    another request is dispatched against the same thread."""

    def __init__(self, inner: Any, may_proceed: threading.Event) -> None:
        self._inner = inner
        self._may_proceed = may_proceed
        self.started = threading.Event()
        self._gated = False

    def get_state(self, config: object) -> object:
        return self._inner.get_state(config)  # type: ignore[attr-defined]

    def invoke(self, request: object, config: object) -> object:
        from langgraph.types import Command

        if isinstance(request, Command) and not self._gated:
            self._gated = True
            self.started.set()
            assert self._may_proceed.wait(timeout=30), "the gated resume was never released"
        return self._inner.invoke(request, config)  # type: ignore[attr-defined]


class _DoorTracingLock:
    """A lock that signals when an acquire found it held — the moment another request is
    standing at the lock's door. Program order puts the pre-fix nothing-pending check
    *before* the acquire, so by the time a waiter is at the door, that check has run;
    after the fix the waiter's check happens later, under the lock."""

    def __init__(self) -> None:
        self._inner = threading.Lock()
        self.waiter_seen = threading.Event()

    def acquire(self, blocking: bool = True) -> bool:
        if self._inner.acquire(blocking=False):
            return True
        self.waiter_seen.set()
        return self._inner.acquire(blocking=blocking)

    def release(self) -> None:
        self._inner.release()

    def __enter__(self) -> "_DoorTracingLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def test_two_overlapping_resumes_answer_exactly_once(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (review round 2): the nothing-pending check ran outside the chat lock,
    so two overlapping resumes both passed it, serialized on the lock, and the loser
    silently replayed the completed turn as its own answer (a no-op ``Command(resume=...)``
    returns the previous turn unchanged). The check now runs under the lock with the
    invoke, so exactly one resume answers and the other is refused with 409
    ``nothing_pending`` — never a replayed turn, and never a new message's turn."""
    from ndis.api import app as app_module

    providers, catalogue = repos
    client, _ = ambiguous_api(providers, catalogue)
    thread_id = client.post("/chat", json={"message": AMBIGUOUS_QUESTION}).json()["thread_id"]
    may_proceed = threading.Event()
    gated = _GatedResumeGraph(
        client.app.dependency_overrides[dependencies.chat_graph](), may_proceed
    )
    client.app.dependency_overrides[dependencies.chat_graph] = lambda: gated
    tracing_lock = _DoorTracingLock()
    monkeypatch.setattr(app_module, "_chat_turn_lock", tracing_lock)

    outcomes: dict[str, object] = {}

    def resume_with(racer: TestClient, option: str, name: str) -> None:
        response = racer.post(f"/chat/{thread_id}/resume", json={"option_id": option})
        outcomes[name] = (response.status_code, response.json())

    first = TestClient(client.app)
    second = TestClient(client.app)
    thread_a = threading.Thread(target=resume_with, args=(first, "qld-4064", "a"))
    thread_a.start()
    # The first resume holds the lock, mid-invoke, turn still pending. Dispatch the
    # second one only now; when it is standing at the lock's door, let the first one
    # finish — in the pre-fix world the second resume's nothing-pending check has
    # already run by then (it sat outside the lock), after the fix it has not.
    assert gated.started.wait(timeout=30)
    thread_b = threading.Thread(target=resume_with, args=(second, "qld-4064", "b"))
    thread_b.start()
    assert tracing_lock.waiter_seen.wait(timeout=30), "the second resume never reached the lock"
    may_proceed.set()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)
    assert set(outcomes) == {"a", "b"}

    (winner_status, winner_body), (loser_status, loser_body) = (
        outcomes["a"],
        outcomes["b"],
    )
    assert sorted([winner_status, loser_status]) == [200, 409]
    if winner_status == 200:
        answered, refused = (winner_status, winner_body), (loser_status, loser_body)
    else:
        answered, refused = (loser_status, loser_body), (winner_status, winner_body)
    # The winner answered the resumed turn; the loser was refused with the machine
    # reason and carries neither an answer nor a replayed one.
    assert answered[1]["status"] == "answered"
    assert answered[1]["answer"]["locality"]["postcode"] == "4064"
    assert refused[0] == 409
    assert refused[1]["detail"]["reason"] == "nothing_pending"
    assert "answer" not in refused[1] or refused[1].get("answer") is None
    # The turn was stored exactly once.
    restored = client.get(f"/chat/{thread_id}").json()
    assert [turn["message"] for turn in restored["turns"]] == [AMBIGUOUS_QUESTION]
    assert restored["pending"] is None
