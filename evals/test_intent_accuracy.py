"""The intent-level golden eval: what the configured model's captured parses imply.

This is the half of the /ask golden gate that needs no database and no endpoint: the
question/payload pairs in ``evals/intent_cache.yaml`` are the recorded answers of the
configured model (the capture is committed, the model comes from the environment), and
each payload is replayed through the real total parser. The filter cases must parse to
exactly the hand-verified filters and the refusal cases to the expected machine refusal
reason, at the floors in ``evals/thresholds.yaml`` — this is the eval that keeps the
prompt contract honest in CI, because it fails when a capture produced by a degraded
prompt (or a degraded capture) is committed.

The end-to-end half — the parsed filters executed against the real register — lives in
``test_ask_golden.py`` and skips where the register snapshot is absent. Skips visibly
while the capture itself is missing on this machine.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from ndis.adapters.golden_questions import read_golden, read_intent_cache
from ndis.adapters.thresholds import read_thresholds
from ndis.domain.intent import (
    REFUSAL_DECISION_REQUEST,
    REFUSAL_EMPTY_INTENT,
    parse_intent,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
CACHE = ROOT / "evals" / "intent_cache.yaml"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"


@pytest.fixture
def payloads() -> Mapping[str, str]:
    if not CACHE.is_file():
        pytest.skip(f"intent cache not captured yet: {CACHE} is missing")
    return read_intent_cache(CACHE)


@pytest.mark.eval
def test_filter_cases_parse_to_the_verified_filters(payloads: Mapping[str, str]) -> None:
    golden = read_golden(GOLDEN)
    matched = 0
    failures: list[str] = []
    for case in golden.filter_cases:
        if case.question not in payloads:
            failures.append(f"{case.question}: no captured payload")
            continue
        parsed = parse_intent(payloads[case.question])
        if parsed.intent is None:
            failures.append(f"{case.question}: unparseable payload: {parsed.rejected}")
        elif parsed.intent.provider_filters != case.filters:
            failures.append(f"{case.question}: {parsed.intent.provider_filters}")
        else:
            matched += 1
    accuracy = matched / len(golden.filter_cases)
    assert accuracy >= read_thresholds(THRESHOLDS).filter_accuracy, "\n".join(failures)


@pytest.mark.eval
def test_refusal_cases_parse_to_the_expected_refusal(payloads: Mapping[str, str]) -> None:
    golden = read_golden(GOLDEN)
    contained = 0
    failures: list[str] = []
    for case in golden.refusal_cases:
        if case.question not in payloads:
            failures.append(f"{case.question}: no captured payload")
            continue
        parsed = parse_intent(payloads[case.question])
        if parsed.intent is None:
            failures.append(f"{case.question}: unparseable payload: {parsed.rejected}")
            continue
        # The same two refusal conditions the Ask use case acts on, evaluated on the
        # replayed intent: a decision request refuses before anything executes, and an
        # empty intent refuses as unmappable.
        if parsed.intent.decision_request:
            reason = REFUSAL_DECISION_REQUEST
        elif parsed.intent.empty():
            reason = REFUSAL_EMPTY_INTENT
        else:
            reason = None
        if reason != case.expected_reason:
            failures.append(f"{case.question}: {reason or 'would execute, not refuse'}")
        else:
            contained += 1
    recall = contained / len(golden.refusal_cases)
    assert recall >= read_thresholds(THRESHOLDS).refusal_recall, "\n".join(failures)
