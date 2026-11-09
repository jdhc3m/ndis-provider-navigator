"""The rule-index builder and the ask-rule use case against fakes: batching, the refusal
ladder, and the deterministic citation gate between the judge and the answer."""

import pytest

from ndis.application.extraction import BudgetExhausted, LlmError, LlmRequest, TokenBudget
from ndis.application.pricing import (
    AskRule,
    BuildRuleIndex,
    EmbeddingError,
    EmbeddingsResult,
    RuleAnswer,
    ScoredChunk,
)
from ndis.domain.pricing import STRATEGY_FIXED, STRATEGY_HEADING, PricingChunk

QUESTION = "Can a provider charge a cancellation fee?"
CHUNK = PricingChunk(
    STRATEGY_FIXED,
    12,
    "A cancellation fee of 100 per cent applies when the provider gives less than "
    "5 business days notice.",
)
CHUNK_B = PricingChunk(STRATEGY_FIXED, 13, "Providers can claim travel under certain conditions.")
QUOTE = "cancellation fee of 100 per cent applies"
JUDGE_OK = (
    '{"supported": true, "answer": "Yes, up to 100 per cent with less than 5 business '
    f'days notice.", "citations": [{{"page": 12, "quote": "{QUOTE}"}}]}}'
)


class FakeEmbedder:
    def __init__(self, vectors: dict[str, tuple[float, ...]] | None = None) -> None:
        self.vectors = vectors or {}
        self.calls: list[list[str]] = []

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(10**9)

    def embed(self, texts: list[str]) -> EmbeddingsResult:
        self.calls.append(list(texts))
        return EmbeddingsResult(
            vectors=tuple(self.vectors.get(text, (1.0, 0.0)) for text in texts),
            input_tokens=sum(len(text) for text in texts) // 4 + 1,
        )


class FakeStore:
    def __init__(self, chunks: tuple[ScoredChunk, ...] = (), count: int | None = None) -> None:
        self.chunks = chunks
        self.count_value = len(chunks) if count is None else count
        self.searches: list[tuple[str, tuple[float, ...], int]] = []
        self.replaced: list[tuple[str, int, int]] = []

    def search(
        self, strategy: str, query_vector: tuple[float, ...], k: int
    ) -> tuple[ScoredChunk, ...]:
        self.searches.append((strategy, query_vector, k))
        return self.chunks

    def count(self, strategy: str) -> int:
        return self.count_value


class FakeLlm:
    def __init__(self, payload: str, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.requests: list[LlmRequest] = []

    @property
    def budget(self) -> TokenBudget:
        return TokenBudget(10**9)

    def complete(self, request: LlmRequest) -> "FakeLlmResult":
        if self.fail:
            raise LlmError("endpoint down")
        self.requests.append(request)
        return FakeLlmResult(self.payload, 100, 50)


class FakeLlmResult:
    def __init__(self, text: str, input_tokens: int, output_tokens: int) -> None:
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def make_ask(
    chunks: tuple[ScoredChunk, ...],
    payload: str = JUDGE_OK,
    *,
    threshold: float = 0.35,
    count: int | None = None,
) -> tuple[AskRule, FakeLlm, FakeEmbedder, FakeStore]:
    embedder = FakeEmbedder({QUESTION: (1.0, 0.0)})
    store = FakeStore(chunks, count=count)
    llm = FakeLlm(payload)
    use_case = AskRule(embedder, store, llm, strategy=STRATEGY_FIXED, threshold=threshold)
    return use_case, llm, embedder, store


def test_answer_with_a_verbatim_citation() -> None:
    use_case, llm, embedder, store = make_ask((ScoredChunk(CHUNK, 0.9),))
    result = use_case(QUESTION)
    assert isinstance(result, RuleAnswer)
    assert result.refused is False
    assert result.reason is None
    assert result.answer and "100 per cent" in result.answer
    assert result.citations == ((12, QUOTE),)
    assert result.top_similarity == 0.9
    assert embedder.calls == [[QUESTION]]
    assert store.searches == [(STRATEGY_FIXED, (1.0, 0.0), 6)]
    assert llm.requests and QUESTION in llm.requests[0].user
    assert "page 12" in llm.requests[0].user


def test_refuses_below_the_similarity_threshold_before_any_llm_call() -> None:
    use_case, llm, _, _ = make_ask((ScoredChunk(CHUNK, 0.2),), threshold=0.35)
    result = use_case(QUESTION)
    assert result == RuleAnswer(
        question=QUESTION,
        answer=None,
        citations=(),
        refused=True,
        reason="below_similarity_threshold",
        top_similarity=0.2,
    )
    assert llm.requests == []


def test_refuses_when_the_judge_says_unsupported() -> None:
    payload = '{"supported": false, "answer": null, "citations": []}'
    use_case, _, _, _ = make_ask((ScoredChunk(CHUNK, 0.9),), payload)
    result = use_case(QUESTION)
    assert result.refused is True
    assert result.reason == "judge_unsupported"


def test_refuses_when_no_citation_survives_the_verbatim_check() -> None:
    payload = (
        '{"supported": true, "answer": "Yes.", '
        '"citations": [{"page": 12, "quote": "a cancellation fee of 90 per cent applies"}]}'
    )
    use_case, _, _, _ = make_ask((ScoredChunk(CHUNK, 0.9),), payload)
    result = use_case(QUESTION)
    assert result.refused is True
    assert result.reason == "no_valid_citation"
    assert result.citations == ()


def test_refuses_when_the_cited_page_is_not_in_the_retrieved_chunks() -> None:
    payload = (
        '{"supported": true, "answer": "Yes.", '
        f'"citations": [{{"page": 99, "quote": "{QUOTE}"}}]}}'
    )
    use_case, _, _, _ = make_ask((ScoredChunk(CHUNK, 0.9),), payload)
    result = use_case(QUESTION)
    assert result.refused is True and result.reason == "no_valid_citation"


def test_refuses_when_the_index_is_empty() -> None:
    use_case, llm, _, _ = make_ask((), count=0)
    result = use_case(QUESTION)
    assert result.refused is True and result.reason == "no_chunks"
    assert llm.requests == []
    assert result.top_similarity is None


def test_refuses_when_the_judge_is_supported_but_gives_no_answer_text() -> None:
    payload = f'{{"supported": true, "citations": [{{"page": 12, "quote": "{QUOTE}"}}]}}'
    use_case, _, _, _ = make_ask((ScoredChunk(CHUNK, 0.9),), payload)
    result = use_case(QUESTION)
    assert result.refused is True and result.reason == "answer_missing"


def test_question_is_stripped_and_empty_question_still_refuses_gracefully() -> None:
    use_case, _, _, _ = make_ask((), count=0)
    result = use_case("   ")
    assert result.question == ""


def test_llm_errors_propagate_for_the_api_to_translate() -> None:
    use_case, llm, _, _ = make_ask((ScoredChunk(CHUNK, 0.9),))
    llm.fail = True
    with pytest.raises(LlmError):
        use_case(QUESTION)


def test_off_topic_question_is_refused_end_to_end() -> None:
    """The acceptance criterion: an off-topic question must be refused, not answered."""
    off_topic = ScoredChunk(CHUNK_B, 0.18)
    use_case, llm, _, _ = make_ask((off_topic,), threshold=0.35)
    result = use_case("What is the capital of France?")
    assert result.refused is True
    assert result.reason == "below_similarity_threshold"
    assert llm.requests == []


# --- Index builder --------------------------------------------------------------------------


class FakeWriter:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int]] = []

    def replace_strategy(
        self, strategy: str, chunks: list[PricingChunk], vectors: list[tuple[float, ...]]
    ) -> None:
        self.writes.append((strategy, len(chunks)))
        assert len(vectors) == len(chunks)


def test_builder_embeds_in_batches_and_writes_once() -> None:
    embedder = FakeEmbedder()
    writer = FakeWriter()
    chunks = tuple(
        PricingChunk(STRATEGY_FIXED, page=page, text=f"chunk {index}")
        for index, page in enumerate(range(1, 6), start=1)
    )
    counts = BuildRuleIndex(embedder, writer)(STRATEGY_FIXED, chunks, batch_size=2)
    assert embedder.calls == [
        ["chunk 1", "chunk 2"],
        ["chunk 3", "chunk 4"],
        ["chunk 5"],
    ]
    assert writer.writes == [(STRATEGY_FIXED, 5)]
    assert counts.chunks == 5 and counts.embedded == 5


def test_builder_budget_exhaustion_propagates() -> None:
    class ExhaustedEmbedder(FakeEmbedder):
        def embed(self, texts: list[str]) -> EmbeddingsResult:
            raise BudgetExhausted("spent")

    with pytest.raises(BudgetExhausted):
        BuildRuleIndex(ExhaustedEmbedder(), FakeWriter())(
            STRATEGY_HEADING, (PricingChunk(STRATEGY_HEADING, 1, "text"),)
        )


def test_builder_embedding_error_propagates() -> None:
    class BrokenEmbedder(FakeEmbedder):
        def embed(self, texts: list[str]) -> EmbeddingsResult:
            raise EmbeddingError("down")

    with pytest.raises(EmbeddingError):
        BuildRuleIndex(BrokenEmbedder(), FakeWriter())(
            STRATEGY_FIXED, (PricingChunk(STRATEGY_FIXED, 1, "text"),)
        )
