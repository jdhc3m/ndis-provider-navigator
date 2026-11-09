"""Ports and use cases for the pricing-rules RAG: build the chunk index, answer a rule
question with page-verified citations, and refuse when the evidence is not there.

The embedding endpoint and the vector store are ports; unit tests inject fakes. Nothing
the model says is trusted: the judge's answer survives only when it claims support AND at
least one citation quote is a verbatim span of a retrieved chunk from the cited page —
deterministic checks in :mod:`ndis.domain.pricing` make the final call.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ndis.application.extraction import (
    LlmClient,
    LlmRequest,
)
from ndis.domain.extraction import TokenBudget
from ndis.domain.pricing import (
    REFUSAL_ANSWER_MISSING,
    PricingChunk,
    check_citations,
    parse_rule_answer,
    refusal_decision,
)

DEFAULT_TOP_K = 6
DEFAULT_EMBED_BATCH = 32

JUDGE_SYSTEM_PROMPT = """\
You answer questions about the NDIS Pricing Arrangements and Price Limits using ONLY the
numbered excerpts provided. Answer with JSON only - no prose, no markdown fence.

Rules:
- "supported" is true only when the excerpts themselves contain the answer. When they do
  not, or only partially, set "supported" to false and leave "answer" null.
- "answer" states the rule concisely in plain English, citing no page numbers inline.
- Every citation is {"page": <the excerpt's page number>, "quote": "<a span copied
  VERBATIM from that excerpt, between 15 and 400 characters>"}. The quote must be ONE
  contiguous run of that excerpt's text, copied character for character with its original
  punctuation. Never stitch together text from different places, never paraphrase, never
  shorten to a heading, and never quote a URL or a page footer.
- Cite only excerpts that directly support the answer; two or three at most.
"""


@dataclass(frozen=True)
class EmbeddingsResult:
    """One embedding batch: a vector per input text, in input order, plus the token usage
    the endpoint reported (or the deterministic estimate when it reports none)."""

    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int


class EmbeddingError(RuntimeError):
    """The embedding endpoint failed for one request in a way no retry could fix."""


class EmbeddingClient(Protocol):
    """One embedding batch against the configured endpoint, with its own pacing, retries
    and per-run token budget."""

    def embed(self, texts: Sequence[str]) -> EmbeddingsResult:
        """One vector per input text; raises ``EmbeddingError`` on permanent failures and
        ``BudgetExhausted`` when the run's budget cannot cover the request."""
        ...

    @property
    def budget(self) -> TokenBudget:
        """The budget this client has spent so far, for run summaries."""
        ...


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieved chunk and its cosine similarity to the question."""

    chunk: PricingChunk
    similarity: float


class RuleVectorStore(Protocol):
    """Read port over the embedded chunk index, queried per chunking strategy."""

    def search(
        self, strategy: str, query_vector: Sequence[float], k: int
    ) -> tuple[ScoredChunk, ...]:
        """The ``k`` chunks of ``strategy`` nearest to ``query_vector``, nearest first."""
        ...

    def count(self, strategy: str) -> int:
        """How many chunks of ``strategy`` the index holds."""
        ...


class RuleIndexWriter(Protocol):
    """Write port for one full rebuild of a strategy's chunk index."""

    def replace_strategy(
        self, strategy: str, chunks: Sequence[PricingChunk], vectors: Sequence[Sequence[float]]
    ) -> None:
        """Drop the stored chunks of ``strategy`` and store these instead."""
        ...


@dataclass(frozen=True)
class IndexCounts:
    """What one index build did, for the script's log line and the report."""

    strategy: str
    chunks: int
    embedded: int
    cached: int
    input_tokens: int


class BuildRuleIndex:
    """Use case: embed a strategy's chunks and write them into the vector store.

    Texts are embedded in ``batch_size`` batches through the bounded embedding client, so
    a free-tier rate limit is met by the adapter's pacing rather than by this loop.
    """

    def __init__(self, client: EmbeddingClient, store: RuleIndexWriter) -> None:
        self._client = client
        self._store = store

    def __call__(
        self,
        strategy: str,
        chunks: Sequence[PricingChunk],
        *,
        batch_size: int = DEFAULT_EMBED_BATCH,
    ) -> IndexCounts:
        vectors: list[tuple[float, ...]] = []
        embedded = 0
        input_tokens = 0
        for start in range(0, len(chunks), batch_size):
            batch = [chunk.text for chunk in chunks[start : start + batch_size]]
            result = self._client.embed(batch)
            vectors.extend(result.vectors)
            embedded += len(batch)
            input_tokens += result.input_tokens
        self._store.replace_strategy(strategy, chunks, vectors)
        return IndexCounts(
            strategy=strategy,
            chunks=len(chunks),
            embedded=embedded,
            cached=0,
            input_tokens=input_tokens,
        )


@dataclass(frozen=True)
class RuleAnswer:
    """The outcome of one rule question: either an answer with page-verified citations or
    a refusal with a machine-readable reason."""

    question: str
    answer: str | None
    citations: tuple[tuple[int, str], ...]
    refused: bool
    reason: str | None
    top_similarity: float | None


def judge_prompt(question: str, excerpts: Sequence[ScoredChunk]) -> str:
    """The user prompt for the judge: the question and the numbered page-tagged excerpts."""
    blocks = "\n\n".join(
        f"[excerpt {index} | page {scored.chunk.page}]\n{scored.chunk.text}"
        for index, scored in enumerate(excerpts, start=1)
    )
    return f"""\
Question: {question}

Answer with one JSON object of this exact shape:
{{"supported": true|false, "answer": "<the rule, or null when unsupported>",
  "citations": [{{"page": <page>, "quote": "<verbatim 15-400 chars>"}}]}}

Excerpts from the NDIS Pricing Arrangements and Price Limits:
{blocks}"""


@dataclass(frozen=True)
class RetrievalRow:
    """One golden question's retrieval outcome for the hit@k measurement."""

    question: str
    expected_page: int
    retrieved_pages: tuple[int, ...]
    top_similarity: float | None

    def hit(self, k: int) -> bool:
        return self.expected_page in self.retrieved_pages[:k]


def retrieval_rows(
    embedder: EmbeddingClient,
    store: RuleVectorStore,
    strategy: str,
    questions: Sequence[tuple[str, int]],
    k: int = 3,
) -> tuple[RetrievalRow, ...]:
    """One retrieval pass over golden (question, expected page) pairs: all questions are
    embedded in one bounded batch, then each is searched on ``strategy``. The report and
    the eval test share this function so the numbers cannot drift apart."""
    if not questions:
        return ()
    vectors = embedder.embed([question for question, _ in questions]).vectors
    rows: list[RetrievalRow] = []
    for (question, expected), vector in zip(questions, vectors, strict=True):
        scored = store.search(strategy, vector, k)
        rows.append(
            RetrievalRow(
                question=question,
                expected_page=expected,
                retrieved_pages=tuple(item.chunk.page for item in scored),
                top_similarity=scored[0].similarity if scored else None,
            )
        )
    return tuple(rows)


def hit_rate(rows: Sequence[RetrievalRow], k: int = 3) -> float | None:
    """The fraction of rows whose expected page appears in the top ``k``; ``None`` when
    there are no rows."""
    if not rows:
        return None
    return sum(1 for row in rows if row.hit(k)) / len(rows)


def lowest_top_similarity(rows: Sequence[RetrievalRow]) -> float | None:
    """The smallest top-chunk similarity over the rows, the floor any refusal threshold
    must sit below so no golden question is refused."""
    similarities = [row.top_similarity for row in rows if row.top_similarity is not None]
    return min(similarities) if similarities else None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AskRule:
    """Use case: answer one question about the pricing rules, or refuse.

    Refuses when nothing is retrieved, when the best similarity is below ``threshold``,
    when the judge says the excerpts do not support an answer, when the answer text is
    missing, or when no citation survives the verbatim-span check in the domain.
    """

    def __init__(
        self,
        embedder: EmbeddingClient,
        store: RuleVectorStore,
        client: LlmClient,
        *,
        strategy: str,
        threshold: float,
        top_k: int = DEFAULT_TOP_K,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._client = client
        self._strategy = strategy
        self._threshold = threshold
        self._top_k = top_k
        self._clock = clock

    def __call__(self, question: str) -> RuleAnswer:
        text = question.strip()
        if not self._store.count(self._strategy):
            return RuleAnswer(text, None, (), True, "no_chunks", None)
        vectors = self._embedder.embed([text]).vectors
        excerpts = self._store.search(self._strategy, vectors[0], self._top_k)
        top_similarity = excerpts[0].similarity if excerpts else None
        if not excerpts:
            return RuleAnswer(text, None, (), True, "no_chunks", top_similarity)
        if top_similarity is not None and top_similarity < self._threshold:
            return RuleAnswer(text, None, (), True, "below_similarity_threshold", top_similarity)
        result = self._client.complete(
            LlmRequest(JUDGE_SYSTEM_PROMPT, judge_prompt(text, excerpts))
        )
        parsed = parse_rule_answer(result.text)
        valid, _ = check_citations(parsed.citations, [scored.chunk for scored in excerpts])
        decision = refusal_decision(
            chunks_found=len(excerpts),
            top_similarity=top_similarity,
            threshold=self._threshold,
            judge_supported=parsed.supported,
            valid_citations=len(valid),
        )
        if decision.refused:
            return RuleAnswer(text, None, (), True, decision.reason, top_similarity)
        answer = parsed.answer
        if answer is None:
            return RuleAnswer(text, None, (), True, REFUSAL_ANSWER_MISSING, top_similarity)
        return RuleAnswer(
            text,
            answer,
            tuple((citation.page, citation.quote) for citation in valid),
            False,
            None,
            top_similarity,
        )
