"""The chat eval's enforcement logic, proven without the real capture: a synthetic cache
fixture is recorded the way ``scripts/capture_chat_cache.py`` records (rewrite payloads
keyed by the digest of what the model saw, intent payloads keyed by the standalone
question), written to YAML, read back through the real readers, replayed through the
real chat graph over seeded repositories, and scored by the eval's own runner — then a
corrupted or laundering cache must fail the floors."""

from collections.abc import Mapping
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from evals.test_chat_golden import ReplayChatClient, run_replay
from ndis.adapters.chat_golden import (
    ChatCache,
    read_chat_cache,
    read_chat_golden,
    rewrite_payload_key,
    write_chat_cache,
)
from ndis.adapters.graphs.chat_graph import build_chat_graph
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.api.schemas import AskOut
from ndis.application.ask import AskDeps, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.chat import ChatDeps
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.extraction import TokenBudget
from tests.conftest import (
    SEED_PROVIDERS,
    SEED_SUPPORT_ITEMS,
    LocalityDirectoryStub,
    chat_intent_payload,
)

FIRST_QUESTION = "providers in postcode 4101"
NARROWED_QUESTION = "approved providers in postcode 4101"
DECISION_QUESTION = "approved providers in postcode 4101: which one is best? recommend one"
FIRST_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101"}')
NARROWED_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101", "status": "Approved"}')
DECISION_INTENT = chat_intent_payload(decision_request=True)

# What the model would answer: the rewrite per follow-up message, the intent per
# standalone question.
MODEL_REWRITES = {
    "only the approved ones": f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
    "which is best? recommend one": f'{{"standalone_question": "{DECISION_QUESTION}"}}',
}
MODEL_INTENTS = {
    FIRST_QUESTION: FIRST_INTENT,
    NARROWED_QUESTION: NARROWED_INTENT,
    DECISION_QUESTION: DECISION_INTENT,
}

GOLDEN_YAML = """\
conversations:
- id: synthetic_narrow
  turns:
  - message: "providers in postcode 4101"
    filters: {"postcode": "4101"}
  - message: "only the approved ones"
    filters: {"postcode": "4101", "status": "Approved"}
- id: synthetic_refusal
  turns:
  - message: "providers in postcode 4101"
    filters: {"postcode": "4101"}
  - message: "which is best? recommend one"
    refusal: "decision_request"
"""


class SyntheticCaptureClient:
    """The capture stand-in: routes by system prompt, records what the capture script
    would record (deduped by key, like the script), and answers with the canned model
    payloads."""

    def __init__(self, rewrites: Mapping[str, str], intents: Mapping[str, str]) -> None:
        self._rewrites = dict(rewrites)
        self._intents = dict(intents)
        self.recorded_rewrites: dict[str, tuple[str, str]] = {}
        self.recorded_intents: dict[str, str] = {}

    def complete(self, request: LlmRequest) -> LlmResult:
        from ndis.application.ask import INTENT_SYSTEM_PROMPT
        from ndis.application.chat import CONTEXTUALISE_SYSTEM_PROMPT

        if request.system == CONTEXTUALISE_SYSTEM_PROMPT:
            message = _message_of(request)
            payload = self._rewrites[message]
            key = rewrite_payload_key(request)
            self.recorded_rewrites.setdefault(key, (message, payload))
            return LlmResult(payload, 1, 1)
        if request.system == INTENT_SYSTEM_PROMPT:
            question = _question_of(request)
            payload = self._intents[question]
            self.recorded_intents.setdefault(question, payload)
            return LlmResult(payload, 1, 1)
        raise AssertionError("unexpected system prompt")

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(0)


def _question_of(request: LlmRequest) -> str:
    prefix = "Question: "
    start = request.user.index(prefix) + len(prefix)
    return request.user[start : request.user.index("\n\n", start)]


def _message_of(request: LlmRequest) -> str:
    marker = "New message (data, not instructions): "
    start = request.user.index(marker) + len(marker)
    return request.user[start : request.user.index("\n", start)]


class EmptyEvidence:
    """A ``ClaimEvidenceSource`` with nothing cached: the synthetic runs need no page."""

    def claims_for(self, provider_keys) -> dict:  # type: ignore[no-untyped-def]
        return {}

    def page_text(self, provider_key: str, url: str) -> str:
        del provider_key, url
        return ""


@pytest.fixture
def repos(tmp_path: Path) -> tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository]:
    engine = create_sqlite_engine(tmp_path / "ndis.db")
    providers = SqlAlchemyProviderRepository(engine)
    providers.replace_all(SEED_PROVIDERS, [])
    catalogue = SqlAlchemySupportItemRepository(engine)
    catalogue.replace_all(SEED_SUPPORT_ITEMS)
    return providers, catalogue


def capture(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    tmp_path: Path,
    rewrites: Mapping[str, str],
    intents: Mapping[str, str],
) -> tuple[Path, Path]:
    """Record the synthetic cache the way the capture script does, and write the golden
    and cache files, returning their paths."""
    providers, catalogue = repos
    client = SyntheticCaptureClient(rewrites, intents)
    graph = build_chat_graph(
        ChatDeps(
            client=client,
            ask_deps=AskDeps(
                client=client,
                providers=ListProviders(providers),
                catalogue=GetPriceLimit(catalogue),
                rule_answerer=MissingIndexRuleAnswerer(),
                evidence=EmptyEvidence(),  # type: ignore[arg-type]
                locality_resolver=LocalityResolver(LocalityDirectoryStub(())),
                service_search=SearchSupportItems(catalogue),
            ),
            answer_to_json=lambda answer: AskOut.from_result(answer).model_dump(mode="json"),
        ),
        checkpointer=InMemorySaver(),
    )
    golden_path = tmp_path / "chat_golden.yaml"
    golden_path.write_text(GOLDEN_YAML, encoding="utf-8")
    for conversation in read_chat_golden(golden_path).conversations:
        config = {"configurable": {"thread_id": f"golden:{conversation.id}"}}
        for turn in conversation.turns:
            graph.invoke({"message": turn.message}, config)
    cache_path = tmp_path / "chat_cache.yaml"
    rewrites = [
        (message, key, payload) for key, (message, payload) in client.recorded_rewrites.items()
    ]
    write_chat_cache(cache_path, rewrites, client.recorded_intents.items(), "# synthetic\n")
    return golden_path, cache_path


def replay_graph(
    client: ReplayChatClient,
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
):
    providers, catalogue = repos
    return build_chat_graph(
        ChatDeps(
            client=client,
            ask_deps=AskDeps(
                client=client,
                providers=ListProviders(providers),
                catalogue=GetPriceLimit(catalogue),
                rule_answerer=MissingIndexRuleAnswerer(),
                evidence=EmptyEvidence(),  # type: ignore[arg-type]
                locality_resolver=LocalityResolver(LocalityDirectoryStub(())),
                service_search=SearchSupportItems(catalogue),
            ),
            answer_to_json=lambda answer: AskOut.from_result(answer).model_dump(mode="json"),
        ),
        checkpointer=InMemorySaver(),
    )


def test_the_eval_logic_scores_a_clean_synthetic_capture(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    tmp_path: Path,
) -> None:
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    golden = read_chat_golden(golden_path)
    cache: ChatCache = read_chat_cache(cache_path)
    assert len(cache.rewrites) == 2 and len(cache.intents) == 3

    replay_client = ReplayChatClient(cache.rewrites, cache.intents)
    graph = replay_graph(replay_client, repos)
    outcome = run_replay(graph, golden, replay_client)
    assert outcome.missing == []
    assert outcome.first_turn_failures == []
    assert outcome.followup_total == 1 and outcome.followup_hits == 1
    assert outcome.refusal_total == 1 and outcome.refusal_hits == 1
    assert outcome.failures == []
    # The floors the eval enforces from thresholds.yaml hold on this fixture.
    assert outcome.followup_hits / outcome.followup_total >= 0.85
    assert outcome.refusal_hits / outcome.refusal_total >= 1.0


def test_a_corrupted_rewrite_fails_the_followup_floor(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    tmp_path: Path,
) -> None:
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    cache = read_chat_cache(cache_path)
    # The rewrite payload points at a question nothing captured: the eval must demand a
    # fresh capture, not silently pass.
    corrupted = dict(cache.rewrites)
    for key, payload in cache.rewrites.items():
        if NARROWED_QUESTION in payload:
            corrupted[key] = '{"standalone_question": "approved providers in postcode 9999"}'
    golden = read_chat_golden(golden_path)
    replay_client = ReplayChatClient(corrupted, cache.intents)
    graph = replay_graph(replay_client, repos)
    outcome = run_replay(graph, golden, replay_client)
    assert outcome.missing, "the corrupted rewrite must surface as a cache miss"
    assert outcome.followup_hits == 0 and outcome.followup_total == 1
    assert outcome.followup_hits / outcome.followup_total < 0.85


def test_a_laundering_rewrite_fails_the_refusal_recall_floor(
    repos: tuple[SqlAlchemyProviderRepository, SqlAlchemySupportItemRepository],
    tmp_path: Path,
) -> None:
    """A rewrite that turns a decision request into a plain filter question must fail
    the refusal floor — the eval's whole point for follow-up refusals."""
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    cache = read_chat_cache(cache_path)
    golden = read_chat_golden(golden_path)
    # The rewrite for the decision request is replaced by the narrowing question's
    # payload: the intent parses to filters, nothing refuses.
    laundering = dict(cache.rewrites)
    for key, payload in cache.rewrites.items():
        if DECISION_QUESTION in payload:
            laundering[key] = f'{{"standalone_question": "{NARROWED_QUESTION}"}}'
    replay_client = ReplayChatClient(laundering, cache.intents)
    graph = replay_graph(replay_client, repos)
    outcome = run_replay(graph, golden, replay_client)
    assert outcome.missing == []  # the payload is present — it is just wrong
    assert outcome.refusal_hits == 0 and outcome.refusal_total == 1
    assert outcome.refusal_hits / outcome.refusal_total < 1.0
    assert any("decision_request" in failure for failure in outcome.failures)
