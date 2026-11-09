"""The /ask golden eval: the twelve filter cases must match the register exactly, and the
five refusal/adversarial cases must be contained with zero executed sections.

The run replays the intent payloads captured by ``scripts/parse_intent_cache.py`` from
the configured model: the parse, the guard and the deterministic execution all run for
real; only the model call is replayed, so this eval spends no quota. Skips visibly while
the register database or the capture is missing on this machine.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.golden_questions import read_golden, read_intent_cache
from ndis.adapters.sqlite_repository import (
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.application.ask import Ask, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders
from ndis.domain.extraction import TokenBudget

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
CACHE = ROOT / "evals" / "intent_cache.yaml"
DB = ROOT / "data" / "ndis.db"
EXTRACTION = ROOT / "data" / "private" / "extraction"
PAGES = ROOT / "data" / "private" / "pages"

# Measured 2026-11-09 against the loaded register: every filter case is an exact match.
EXPECTED_FILTER_ACCURACY = 1.0
FILTER_PAGE = 5


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
def ask(payloads: dict[str, str]) -> tuple[Ask, CachedIntentClient]:
    engine = create_sqlite_engine(DB)
    client = CachedIntentClient(payloads)
    use_case = Ask(
        client,
        ListProviders(SqlAlchemyProviderRepository(engine)),
        GetPriceLimit(SqlAlchemySupportItemRepository(engine)),
        MissingIndexRuleAnswerer(),
        _evidence(engine),
        provider_limit=FILTER_PAGE,
    )
    return use_case, client


def _evidence(engine: Engine) -> ExtractionEvidenceSource:
    return ExtractionEvidenceSource(engine, EXTRACTION, PAGES)


@pytest.mark.eval
def test_filter_cases_match_the_register_exactly(
    ask: tuple[Ask, CachedIntentClient],
) -> None:
    use_case, client = ask
    golden = read_golden(GOLDEN)
    matched = 0
    failures: list[str] = []
    for case in golden.filter_cases:
        answer = use_case(case.question)
        problems: list[str] = []
        if answer.refused:
            problems.append(f"refused:{answer.reason}")
        if answer.intent is None or answer.intent.provider_filters != case.filters:
            problems.append(f"intent:{answer.intent}")
        if answer.provider_total != case.total:
            problems.append(f"total:{answer.provider_total}!={case.total}")
        names = [provider.business_name for provider in answer.providers]
        if names != list(case.first):
            problems.append(f"first:{names}")
        if answer.caveat is not None:
            problems.append("caveat")
        if problems:
            failures.append(f"{case.question}: {', '.join(problems)}")
        else:
            matched += 1
    accuracy = matched / len(golden.filter_cases)
    assert accuracy == EXPECTED_FILTER_ACCURACY, "\n".join(failures)
    assert client.asked == [case.question for case in golden.filter_cases]


@pytest.mark.eval
def test_refusal_cases_are_contained(ask: tuple[Ask, CachedIntentClient]) -> None:
    use_case, client = ask
    golden = read_golden(GOLDEN)
    for case in golden.refusal_cases:
        answer = use_case(case.question)
        assert answer.refused, f"not refused: {case.question}"
        assert answer.reason == case.expected_reason, case.question
        assert answer.explanation is not None, case.question
        # Containment: zero executed sections, no services, no caveat, no ungrounded text.
        assert answer.providers == (), case.question
        assert answer.provider_total == 0, case.question
        assert dict(answer.services) == {}, case.question
        assert answer.price is None and answer.price_error is None, case.question
        assert answer.rule is None, case.question
        assert answer.caveat is None, case.question
    assert client.asked == [case.question for case in golden.refusal_cases]


@pytest.mark.eval
def test_every_payload_parses_to_a_recognised_intent(payloads: Mapping[str, str]) -> None:
    """Replay the captured payloads through the parser alone: a payload that stops
    parsing after a model change must surface here, not as an eval surprise."""
    from ndis.domain.intent import parse_intent

    golden = read_golden(GOLDEN)
    for case in (*golden.filter_cases, *golden.refusal_cases):
        parsed = parse_intent(payloads[case.question])
        assert parsed.intent is not None, f"{case.question}: {parsed.rejected}"
