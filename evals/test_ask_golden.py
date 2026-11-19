"""The /ask golden eval: the twelve filter cases and the seven locality cases must match
the register exactly, and the six refusal/adversarial cases must be contained with zero
executed sections.

The run replays the intent payloads captured by ``scripts/parse_intent_cache.py`` from
the configured model: the parse, the guard and the deterministic execution all run for
real; only the model call is replayed, so this eval spends no quota. Skips visibly while
the register database or the capture is missing on this machine. The database file comes
from ``NDIS_DB_PATH`` (see ``ndis.api.dependencies``), defaulting to ``data/ndis.db``.
"""

import os
from collections.abc import Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from ndis.adapters.ask_evidence import ExtractionEvidenceSource
from ndis.adapters.golden_questions import read_golden, read_intent_cache
from ndis.adapters.sqlite_repository import (
    SqlAlchemyPostcodeCentroids,
    SqlAlchemyProviderRepository,
    SqlAlchemySupportItemRepository,
    create_sqlite_engine,
)
from ndis.adapters.thresholds import read_thresholds
from ndis.application.ask import Ask, MissingIndexRuleAnswerer
from ndis.application.catalogue import GetPriceLimit
from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.providers import ListProviders, LocalityResolver
from ndis.domain.extraction import TokenBudget
from ndis.domain.locality import LocalityOutcome

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
CACHE = ROOT / "evals" / "intent_cache.yaml"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"
DB_PATH_ENV = "NDIS_DB_PATH"
DB = Path(os.environ.get(DB_PATH_ENV) or ROOT / "data" / "ndis.db")
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
        locality_resolver=LocalityResolver(SqlAlchemyPostcodeCentroids(engine)),
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
    contained = 0
    failures: list[str] = []
    for case in golden.refusal_cases:
        answer = use_case(case.question)
        problems: list[str] = []
        if not answer.refused:
            problems.append("not_refused")
        elif answer.reason != case.expected_reason:
            problems.append(f"reason:{answer.reason}!={case.expected_reason}")
        if answer.explanation is None:
            problems.append("no_explanation")
        # Containment: zero executed sections, no services, no caveat, no ungrounded text.
        if answer.providers != ():
            problems.append("providers")
        if answer.provider_total != 0:
            problems.append("provider_total")
        if dict(answer.services) != {}:
            problems.append("services")
        if answer.price is not None or answer.price_error is not None:
            problems.append("price")
        if answer.rule is not None:
            problems.append("rule")
        if answer.caveat is not None:
            problems.append("caveat")
        if problems:
            failures.append(f"{case.question}: {', '.join(problems)}")
        else:
            contained += 1
    recall = contained / len(golden.refusal_cases)
    assert recall >= read_thresholds(THRESHOLDS).refusal_recall, "\n".join(failures)
    assert client.asked == [case.question for case in golden.refusal_cases]


@pytest.mark.eval
def test_locality_cases_match_the_register_and_the_gazetteer(
    ask: tuple[Ask, CachedIntentClient],
) -> None:
    """The suburb questions resolve in code: a resolved name returns exactly the same
    page as its postcode question and discloses the resolution; an ambiguous or unknown
    name runs no provider query and discloses the reason and the candidates."""
    use_case, client = ask
    golden = read_golden(GOLDEN)
    matched = 0
    failures: list[str] = []
    for case in golden.locality_cases:
        answer = use_case(case.question)
        problems: list[str] = []
        if answer.refused:
            problems.append(f"refused:{answer.reason}")
        if answer.intent is None or answer.intent.provider_filters != case.filters:
            problems.append(f"intent:{answer.intent}")
        resolution = answer.locality
        if resolution is None:
            problems.append("no_resolution")
        elif case.outcome == "resolved":
            if resolution.outcome is not LocalityOutcome.RESOLVED:
                problems.append(f"outcome:{resolution.outcome}")
            elif resolution.locality is None:
                problems.append("no_locality")
            else:
                point = resolution.locality
                if answer.provider_total != case.total:
                    problems.append(f"total:{answer.provider_total}!={case.total}")
                names = [provider.business_name for provider in answer.providers]
                if names != list(case.first):
                    problems.append(f"first:{names}")
                postcodes = {provider.head_office.postcode for provider in answer.providers}
                if point.postcode not in postcodes | {None}:
                    problems.append(f"postcode:{point.postcode}")
        else:
            if resolution.outcome.value != case.outcome:
                problems.append(f"outcome:{resolution.outcome}")
            if answer.providers != () or answer.provider_total != 0:
                problems.append("ran_provider_query")
            pairs = {(item.state, item.postcode) for item in resolution.candidates}
            if pairs != set(case.candidates):
                problems.append(f"candidates:{sorted(pairs)}")
            steps = {step.kind: step.output_summary for step in answer.steps}
            if case.outcome not in steps.get("provider_filter", ""):
                problems.append("step_does_not_say_why")
        if problems:
            failures.append(f"{case.question}: {', '.join(problems)}")
        else:
            matched += 1
    assert matched == len(golden.locality_cases), "\n".join(failures)
    assert client.asked == [case.question for case in golden.locality_cases]


@pytest.mark.eval
def test_the_suburb_refusal_case_does_not_execute(
    ask: tuple[Ask, CachedIntentClient],
) -> None:
    """The suburb question that asks for a recommendation refuses before anything runs,
    even though the question also names a resolvable suburb."""
    use_case, _ = ask
    golden = read_golden(GOLDEN)
    suburb_refusals = [
        case
        for case in golden.refusal_cases
        if "West End" in case.question and case.expected_reason == "decision_request"
    ]
    assert suburb_refusals, "the golden set lost its suburb refusal case"
    for case in suburb_refusals:
        answer = use_case(case.question)
        assert answer.refused and answer.reason == "decision_request"
        assert answer.providers == () and answer.provider_total == 0
        assert answer.services == {} and answer.price is None and answer.rule is None
        assert answer.locality is None  # refused before any resolution ran
