"""The chat eval's enforcement logic, proven without the real capture: a synthetic cache
fixture is recorded the way ``scripts/capture_chat_cache.py`` records (rewrite payloads
keyed by the digest of what the model saw, intent payloads keyed by the standalone
question, and per turn the summary the rewrite prompt carried), written to YAML, read
back through the real readers, replayed through the real chat graph over seeded
repositories, and scored by the eval's own runner — then a corrupted or laundering cache
must fail the floors, and the replay's rewrite lookups must not care how its own ask
sections are wired, because the history comes verbatim from the cached summaries."""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from evals.test_chat_golden import (
    CachedSummarizer,
    ReplayChatClient,
    ReplayRepos,
    fixture_chat_deps,
    run_replay,
    seed_replay_repos,
)
from ndis.adapters.chat_golden import (
    ChatCache,
    ChatGoldenFormatError,
    read_chat_cache,
    read_chat_golden,
    rewrite_payload_key,
    write_chat_cache,
)
from ndis.adapters.graphs.chat_graph import build_chat_graph
from ndis.application.ask import ASK_PROVIDER_LIMIT
from ndis.application.chat import turn_summary
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.domain.extraction import TokenBudget
from ndis.domain.intent import ProviderFilters
from tests.conftest import chat_intent_payload

FIRST_QUESTION = "providers in postcode 4101"
NARROWED_QUESTION = "approved providers in postcode 4101"
DECISION_QUESTION = "approved providers in postcode 4101: which one is best? recommend one"
FIRST_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101"}')
NARROWED_INTENT = chat_intent_payload(provider_filters='{"postcode": "4101", "status": "Approved"}')
DECISION_INTENT = chat_intent_payload(decision_request=True)

# What the model would answer: the rewrite per follow-up message, the intent per
# standalone question.
SERVICE_QUESTION = "how much does occupational therapy cost for providers in postcode 4101?"
SERVICE_FOLLOWUP = "how much does occupational therapy cost?"
COMBINED_INTENT = chat_intent_payload(
    provider_filters='{"postcode": "4101"}',
    price_query='{"service": "occupational therapy"}',
)

MODEL_REWRITES = {
    "only the approved ones": f'{{"standalone_question": "{NARROWED_QUESTION}"}}',
    "which is best? recommend one": f'{{"standalone_question": "{DECISION_QUESTION}"}}',
    SERVICE_FOLLOWUP: f'{{"standalone_question": "{SERVICE_QUESTION}"}}',
}
MODEL_INTENTS = {
    FIRST_QUESTION: FIRST_INTENT,
    NARROWED_QUESTION: NARROWED_INTENT,
    DECISION_QUESTION: DECISION_INTENT,
    SERVICE_QUESTION: COMBINED_INTENT,
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
- id: synthetic_service_with_place
  turns:
  - message: "providers in postcode 4101"
    filters: {"postcode": "4101"}
  - message: "how much does occupational therapy cost?"
    filters: {"postcode": "4101"}
    service: "occupational therapy"
    service_state: null
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


@pytest.fixture
def repos(tmp_path: Path) -> ReplayRepos:
    """The seeded temp repositories the synthetic capture and every replay run over."""
    return seed_replay_repos(tmp_path)


def capture(
    repos: ReplayRepos,
    tmp_path: Path,
    rewrites: Mapping[str, str],
    intents: Mapping[str, str],
    *,
    provider_limit: int | None = None,
) -> tuple[Path, Path]:
    """Record the synthetic cache the way the capture script does (the served pipeline:
    history summarized from the turns it holds, at the given provider page), and write
    the golden and cache files, returning their paths."""
    client = SyntheticCaptureClient(rewrites, intents)
    deps = fixture_chat_deps(client, repos)
    if provider_limit is not None:
        deps = replace(deps, ask_deps=replace(deps.ask_deps, provider_limit=provider_limit))
    graph = build_chat_graph(deps, checkpointer=InMemorySaver())
    golden_path = tmp_path / "chat_golden.yaml"
    golden_path.write_text(GOLDEN_YAML, encoding="utf-8")
    summaries: dict[str, dict[str, object]] = {}
    for conversation in read_chat_golden(golden_path).conversations:
        config = {"configurable": {"thread_id": f"golden:{conversation.id}"}}
        for turn in conversation.turns:
            final = graph.invoke({"message": turn.message}, config)
            stored = final["turns"][-1]
            summaries.setdefault(stored["standalone_question"], turn_summary(stored))
    cache_path = tmp_path / "chat_cache.yaml"
    recorded = [
        (message, key, payload) for key, (message, payload) in client.recorded_rewrites.items()
    ]
    write_chat_cache(
        cache_path, recorded, client.recorded_intents.items(), "# synthetic\n", summaries=summaries
    )
    return golden_path, cache_path


def replay(
    cache: ChatCache,
    repos: ReplayRepos,
    *,
    rewrites: Mapping[str, str] | None = None,
    intents: Mapping[str, str] | None = None,
    summaries: Mapping[str, Mapping[str, object]] | None = None,
    provider_limit: int | None = None,
) -> tuple[Any, ReplayChatClient, CachedSummarizer]:
    """The replay graph over the seeded repositories at the given wiring, replaying the
    loaded cache (a test may corrupt one section by passing that section's replacement)."""
    client = ReplayChatClient(
        rewrites if rewrites is not None else cache.rewrites,
        intents if intents is not None else cache.intents,
    )
    summarizer = CachedSummarizer(summaries if summaries is not None else cache.summaries)
    deps = fixture_chat_deps(client, repos, summarize_turns=summarizer)
    if provider_limit is not None:
        deps = replace(deps, ask_deps=replace(deps.ask_deps, provider_limit=provider_limit))
    graph = build_chat_graph(deps, checkpointer=InMemorySaver())
    return graph, client, summarizer


def test_the_eval_logic_scores_a_clean_synthetic_capture(
    repos: ReplayRepos,
    tmp_path: Path,
) -> None:
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    golden = read_chat_golden(golden_path)
    cache = read_chat_cache(cache_path)
    assert len(cache.rewrites) == 3 and len(cache.intents) == 4
    # One summary per unique standalone question: three first turns share one question,
    # and each follow-up rewrote into its own.
    assert len(cache.summaries) == 4
    assert set(cache.summaries) == {
        FIRST_QUESTION,
        NARROWED_QUESTION,
        DECISION_QUESTION,
        SERVICE_QUESTION,
    }

    graph, replay_client, summarizer = replay(cache, repos)
    outcome = run_replay(graph, golden, replay_client, summarizer)
    assert outcome.missing == []
    assert outcome.first_turn_failures == []
    assert outcome.followup_total == 2 and outcome.followup_hits == 2
    assert outcome.refusal_total == 1 and outcome.refusal_hits == 1
    assert outcome.failures == []
    # The floors the eval enforces from thresholds.yaml hold on this fixture.
    assert outcome.followup_hits / outcome.followup_total >= 0.85
    assert outcome.refusal_hits / outcome.refusal_total >= 1.0


def test_the_replays_rewrite_lookups_ignore_its_own_ask_wiring(
    repos: ReplayRepos,
    tmp_path: Path,
) -> None:
    """Regression guard for the new shape: the capture ran at one provider page size,
    the replay deliberately runs at another — its execution shows different providers,
    and the rewrite digests must still resolve, because the rewrite prompt's history is
    byte-identical from the cached summaries and nothing else about it depends on the
    replay's wiring. (Before the summaries existed, exactly this mismatch missed every
    follow-up lookup — review round 2.)"""
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    cache = read_chat_cache(cache_path)
    golden = read_chat_golden(golden_path)

    graph, replay_client, summarizer = replay(cache, repos, provider_limit=1)
    outcome = run_replay(graph, golden, replay_client, summarizer)
    assert outcome.missing == []
    assert outcome.followup_hits == outcome.followup_total == 2
    assert outcome.refusal_hits == outcome.refusal_total == 1
    assert outcome.failures == []


def test_a_missing_summary_fails_loudly_through_the_cache_misses(
    repos: ReplayRepos,
    tmp_path: Path,
) -> None:
    """A cache without the summaries degrades, not crashes: the rebuilt history differs
    from capture time, every rewrite digest misses, and the recorded misses fail the
    eval instead of scoring a silent 1.0."""
    golden_path, cache_path = capture(repos, tmp_path, MODEL_REWRITES, MODEL_INTENTS)
    cache = read_chat_cache(cache_path)
    golden = read_chat_golden(golden_path)

    graph, replay_client, summarizer = replay(cache, repos, summaries={})
    outcome = run_replay(graph, golden, replay_client, summarizer)
    assert outcome.missing, "the missing summaries must surface as cache misses"
    assert any("summary for" in miss for miss in outcome.missing)
    assert outcome.followup_hits < outcome.followup_total


def test_a_corrupted_rewrite_fails_the_followup_floor(
    repos: ReplayRepos,
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
    graph, replay_client, summarizer = replay(cache, repos, rewrites=corrupted)
    outcome = run_replay(graph, golden, replay_client, summarizer)
    assert outcome.missing, "the corrupted rewrite must surface as a cache miss"
    assert outcome.followup_hits < outcome.followup_total  # the poisoned turn scores 0
    assert outcome.followup_hits / outcome.followup_total < 0.85


def test_a_laundering_rewrite_fails_the_refusal_recall_floor(
    repos: ReplayRepos,
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
    graph, replay_client, summarizer = replay(cache, repos, rewrites=laundering)
    outcome = run_replay(graph, golden, replay_client, summarizer)
    assert outcome.missing == []  # the payload is present — it is just wrong
    assert outcome.refusal_hits == 0 and outcome.refusal_total == 1
    assert outcome.refusal_hits / outcome.refusal_total < 1.0
    assert any("decision_request" in failure for failure in outcome.failures)


def test_the_capture_builder_runs_at_the_served_provider_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capture must see what a user of the live /chat endpoint sees: the same
    ``ASK_PROVIDER_LIMIT`` provider page — it decides which providers the captured
    history summaries list. The replay no longer shares this builder (its prompts are
    pinned by the cached summaries; see the wiring-invariance test above)."""
    import scripts.capture_chat_cache as capture_module

    monkeypatch.setattr(capture_module, "provider_repository", lambda: object())
    monkeypatch.setattr(capture_module, "postcode_centroids", lambda: object())
    monkeypatch.setattr(capture_module, "support_item_repository", lambda: object())
    monkeypatch.setattr(capture_module, "claim_evidence_source", lambda: object())
    monkeypatch.setattr(capture_module, "locality_resolver", lambda: object())
    capture_deps = capture_module.build_capture_deps(_DummyClient())
    assert capture_deps.ask_deps.provider_limit == ASK_PROVIDER_LIMIT


class _DummyClient:
    """A stand-in client for building the capture deps without touching the network."""

    @property
    def budget(self) -> object:
        return None

    def complete(self, request: object) -> object:
        raise AssertionError("building the deps never completes a call")


def test_the_reader_allows_filters_alongside_a_service(tmp_path: Path) -> None:
    """The rewrite copies the place forward, so a price follow-up may answer the
    combined shape (filters plus the price query) — the golden file expects both."""
    path = tmp_path / "chat_golden.yaml"
    path.write_text(
        """\
conversations:
- id: combined
  turns:
  - message: "Which providers are in Brisbane City?"
    filters: {"locality": "Brisbane City"}
  - message: "how much does occupational therapy cost per hour?"
    filters: {"locality": "Brisbane City"}
    service: "occupational therapy"
    service_state: null
""",
        encoding="utf-8",
    )
    golden = read_chat_golden(path)
    follow_up = golden.conversations[0].turns[1]
    assert follow_up.filters == ProviderFilters(locality="Brisbane City")
    assert follow_up.service == "occupational therapy"
    assert follow_up.service_state is None and follow_up.refusal is None


@pytest.mark.parametrize(
    "combination",
    [
        'refusal: "decision_request"\n    service: "occupational therapy"',
        'rule_contains: "cancellation"\n    filters: {"state": "QLD"}',
        'rule_contains: "cancellation"\n    service: "occupational therapy"',
        'refusal: "decision_request"\n    filters: {"state": "QLD"}',
    ],
)
def test_the_reader_still_rejects_impossible_combinations(tmp_path: Path, combination: str) -> None:
    path = tmp_path / "chat_golden.yaml"
    path.write_text(
        f"""\
conversations:
- id: broken
  turns:
  - message: "Which providers are in Brisbane City?"
    filters: {{"locality": "Brisbane City"}}
  - message: "how much does occupational therapy cost per hour?"
    {combination}
""",
        encoding="utf-8",
    )
    with pytest.raises(ChatGoldenFormatError):
        read_chat_golden(path)
