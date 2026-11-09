"""The extraction use case against fakes: cache skipping, page caps, failure and budget
semantics, and the deterministic rejection of model output."""

from datetime import UTC, datetime

from ndis.application.extraction import (
    BudgetExhausted,
    ExtractionStore,
    ExtractServices,
    LlmError,
    LlmRequest,
    LlmResult,
)
from ndis.domain.extraction import (
    ProviderExtraction,
    StoredPage,
    TokenBudget,
    quote_supported,
)
from ndis.domain.taxonomy import Service

PHYSIO = Service.PHYSIOTHERAPY
QUOTE = "We provide physiotherapy and exercise programs for NDIS participants"

PAGE_TEXT = (
    "Gulf Physio cares for NDIS participants. We provide physiotherapy and exercise "
    "programs for NDIS participants in Brisbane. Mobile visits available. "
    "Telehealth appointments on request."
)

# A model answer whose quote is a genuine substring of PAGE_TEXT.
GOOD_ANSWER = (
    '{"claims": [{"service": "physiotherapy", "evidence_quote": "'
    + QUOTE
    + '", "confidence": 0.9}], "telehealth": true}'
)
BAD_QUOTE_ANSWER = (
    '{"claims": [{"service": "nursing", "evidence_quote": '
    '"Our nurses deliver 24/7 wound and catheter care", "confidence": 0.9}]}'
)
DUPLICATE_ANSWER = (
    '{"claims": [{"service": "physiotherapy", "evidence_quote": "'
    + QUOTE
    + '", "confidence": 0.9}, {"service": "physiotherapy", "evidence_quote": "'
    + QUOTE
    + '", "confidence": 0.8}]}'
)
NOT_JSON = "I cannot answer that."


class FakeClient:
    """A scripted LLMClient: pops one answer per call; can fail or exhaust the budget."""

    def __init__(self, answers: list[str], *, error_on: int | None = None) -> None:
        self._answers = list(answers)
        self._error_on = error_on
        self.requests: list[LlmRequest] = []
        self._budget = TokenBudget(10**9)

    def complete(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        if self._error_on is not None and len(self.requests) == self._error_on:
            raise LlmError("endpoint gone")
        if not self._answers:
            raise AssertionError("no scripted answer left")
        return LlmResult(self._answers.pop(0), 100, 20)

    @property
    def budget(self) -> TokenBudget:
        return self._budget


class FakeStore:
    def __init__(self, cached: frozenset[str] = frozenset()) -> None:
        self._cached = cached
        self.saved: list[ProviderExtraction] = []

    def cached_provider_keys(self) -> frozenset[str]:
        return frozenset(key for key, _ in [(p.provider_key, None) for p in self.saved]) | {
            key for key in self._cached
        }

    def save(self, profile: ProviderExtraction) -> None:
        self.saved.append(profile)

    def load(self, provider_key: str) -> ProviderExtraction | None:
        for profile in self.saved:
            if profile.provider_key == provider_key:
                return profile
        return None


class FakeSource:
    def __init__(self, stored: tuple[StoredPage, ...]) -> None:
        self._stored = stored

    def ok_pages(self) -> tuple[StoredPage, ...]:
        return self._stored


def pages(*keys: str) -> tuple[StoredPage, ...]:
    return tuple(
        StoredPage(
            key,
            f"https://{key}.example.com/{suffix}",
            key,
            PAGE_TEXT if suffix == "" else PAGE_TEXT + f" Also {suffix} details follow.",
        )
        for key in keys
        for suffix in ("", "services")
    )


def now() -> datetime:
    return datetime.now(UTC)


def test_extracts_accepts_quotes_and_merges_pages() -> None:
    client = FakeClient([GOOD_ANSWER, GOOD_ANSWER])
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.extracted == 1 and counts.failed == 0 and counts.pages_sent == 2
    profile = store.saved[0]
    assert profile.provider_key == "alpha"
    assert profile.services() == frozenset({PHYSIO})
    assert all(quote_supported(PAGE_TEXT, c.claim.evidence_quote) for c in profile.claims)
    assert profile.attributes.telehealth is True
    assert counts.claims_accepted == 2


def test_cached_providers_are_never_sent() -> None:
    client = FakeClient([])
    store = FakeStore(cached=frozenset({"alpha"}))
    counts = ExtractServices(FakeSource(pages("alpha", "beta")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.providers == 1 and counts.cached == 1 and counts.extracted == 0
    assert client.requests == []


def test_one_failed_page_leaves_the_provider_uncached() -> None:
    client = FakeClient([GOOD_ANSWER, NOT_JSON], error_on=2)
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.extracted == 0 and counts.failed == 1
    assert store.saved == []


def test_unsupported_quotes_are_counted_not_stored() -> None:
    client = FakeClient([BAD_QUOTE_ANSWER, BAD_QUOTE_ANSWER])
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.claims_accepted == 0
    assert counts.claims_rejected == 2
    profile = store.saved[0]
    assert profile.services() == frozenset()
    assert profile.claims == ()


def test_duplicate_claims_are_counted_as_duplicates_not_unsupported() -> None:
    client = FakeClient([DUPLICATE_ANSWER, DUPLICATE_ANSWER])
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.claims_accepted == 2
    assert counts.claims_rejected == 0  # a duplicate is not an unsupported quote
    assert counts.rejections == {"claim_duplicate": 2}
    profile = store.saved[0]
    assert len(profile.claims) == 2  # one kept claim per page; the projection folds repeats
    assert all(claim.claim.confidence == 0.9 for claim in profile.claims)


def test_budget_exhaustion_stops_the_run_and_keeps_completed_providers() -> None:
    class ExhaustingClient(FakeClient):
        def complete(self, request: LlmRequest) -> LlmResult:
            if len(self.requests) >= 2:
                raise BudgetExhausted("budget gone")
            return super().complete(request)

    client = ExhaustingClient([GOOD_ANSWER, GOOD_ANSWER])
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha", "beta")), client, store, clock=now)()
    assert counts.budget_exhausted is True
    assert counts.extracted == 1 and counts.failed == 0  # alpha done, beta aborted
    assert [p.provider_key for p in store.saved] == ["alpha"]


def test_page_cap_limits_model_calls() -> None:
    many = tuple(
        StoredPage("alpha", f"https://alpha.example.com/page{i}", None, f"text {i}")
        for i in range(20)
    )
    client = FakeClient([GOOD_ANSWER] * 30)
    store = FakeStore()
    counts = ExtractServices(FakeSource(many), client, store, max_pages=3, clock=now)(
        provider_keys=["alpha"]
    )
    assert counts.pages_sent == 3 and counts.extracted == 1 and counts.failed == 0


def test_limit_processes_providers_in_chunks() -> None:
    client = FakeClient([GOOD_ANSWER] * 10)
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha", "beta")), client, store, clock=now)(limit=1)
    assert counts.extracted == 1
    assert len(store.saved) == 1


def test_summary_line_reports_counts() -> None:
    client = FakeClient([BAD_QUOTE_ANSWER, BAD_QUOTE_ANSWER])
    store = FakeStore()
    counts = ExtractServices(FakeSource(pages("alpha")), client, store, clock=now)(
        provider_keys=["alpha"]
    )
    text = counts.summary()
    assert "claims 0 accepted, 2 unsupported" in text
    assert "claim_quote_not_in_page=2" in text  # unsupported quotes carry their own reason
    assert "not_json" not in text  # parse rejects are a different bucket and did not happen


def test_store_port_is_satisfied_by_fakes() -> None:
    store: ExtractionStore = FakeStore()
    assert store.cached_provider_keys() == frozenset()
