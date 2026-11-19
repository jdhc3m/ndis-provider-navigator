"""Read ``evals/golden.yaml``: the ten pricing-rule questions with source-verified expected
pages plus the off-topic refusal case, the twelve /ask filter cases with their exact
expected register results, and the five refusal/adversarial cases.

The file is the same small, fixed YAML subset the other eval files use: a comment header
and top-level sections whose block mappings carry JSON-quoted string values. ``page`` is
the printed page of the pricing PDF the answer's evidence lives on, verified against the
actual document; the filter cases' ``total`` and ``first`` were verified against the
loaded register database.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass

from ndis.domain.intent import ProviderFilters

FIELDS = ("question", "page", "topic", "evidence", "verified_by")
OFF_TOPIC_FIELDS = ("question", "reason_expected")
FILTER_FIELDS = ("question", "filters", "total", "first")
REFUSAL_FIELDS = ("question", "expected_reason", "adversarial")
LOCALITY_FIELDS = ("question", "filters", "outcome", "total", "first", "candidates")
SECTIONS = ("questions", "off_topic", "filter_cases", "locality_cases", "refusal_cases")
INTENT_FIELDS = ("question", "payload")


class GoldenFormatError(ValueError):
    """The golden file does not follow the layout this module reads."""


@dataclass(frozen=True)
class GoldenQuestion:
    """One rule question and the page its answer's evidence is printed on."""

    question: str
    page: int
    topic: str
    evidence: str
    verified_by: str


@dataclass(frozen=True)
class GoldenFilterCase:
    """One /ask filter case: the question, the filters a correct intent parse must
    produce, and the exact register results the execution must return."""

    question: str
    filters: ProviderFilters
    total: int
    first: tuple[str, ...]


@dataclass(frozen=True)
class GoldenRefusalCase:
    """One refusal or adversarial /ask case: the question and the machine reason a
    contained answer must refuse with."""

    question: str
    expected_reason: str
    adversarial: bool


@dataclass(frozen=True)
class GoldenLocalityCase:
    """One /ask locality case (ticket 13): the question, the filters a correct intent
    parse must produce, and the expected resolution. ``outcome`` is ``resolved`` (with
    the exact register ``total`` and the ``first`` page of business names), or
    ``locality_unknown`` / ``locality_ambiguous`` (with the ``candidates`` the answer
    must disclose and no provider query at all)."""

    question: str
    filters: ProviderFilters
    outcome: str
    total: int | None = None
    first: tuple[str, ...] = ()
    candidates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class GoldenSet:
    """The whole golden file: the rule questions, the off-topic refusal case, the /ask
    filter cases, the /ask locality cases and the /ask refusal cases."""

    questions: tuple[GoldenQuestion, ...]
    off_topic_question: str
    off_topic_reason: str
    filter_cases: tuple[GoldenFilterCase, ...] = ()
    locality_cases: tuple[GoldenLocalityCase, ...] = ()
    refusal_cases: tuple[GoldenRefusalCase, ...] = ()


def _parse_scalar(text: str) -> object:
    text = text.strip()
    if text in ("null", "~", ""):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def read_golden(path) -> GoldenSet:  # type: ignore[no-untyped-def]
    """Parse the file; raises ``GoldenFormatError`` on unknown fields or wrong shapes."""
    rows: list[dict[str, object]] = []
    filter_rows: list[dict[str, object]] = []
    locality_rows: list[dict[str, object]] = []
    refusal_rows: list[dict[str, object]] = []
    off_topic: dict[str, object] | None = None
    section: str | None = None
    current: dict[str, object] | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.endswith(":") and not line.startswith(" "):
            name = line[:-1]
            if name not in SECTIONS:
                raise GoldenFormatError(f"{path}:{number}: unknown section {line!r}")
            section = name
            current = {} if section != "off_topic" else None
            if section == "off_topic":
                off_topic = {}
            continue
        if section == "off_topic":
            if off_topic is None or not line.startswith("  ") or ":" not in line:
                raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
            field, _, value = line.strip().partition(":")
            if field not in OFF_TOPIC_FIELDS:
                raise GoldenFormatError(f"{path}:{number}: unknown field {field!r}")
            off_topic[field] = _parse_scalar(value)
            continue
        if section is None:
            raise GoldenFormatError(f"{path}:{number}: line {raw!r} outside any section")
        if line.startswith("- "):
            current = {}
            {
                "questions": rows,
                "filter_cases": filter_rows,
                "locality_cases": locality_rows,
                "refusal_cases": refusal_rows,
            }[section].append(current)
            line = "  " + line[2:]
        if current is None or not line.startswith("  ") or ":" not in line:
            raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        allowed = {
            "questions": FIELDS,
            "filter_cases": FILTER_FIELDS,
            "locality_cases": LOCALITY_FIELDS,
            "refusal_cases": REFUSAL_FIELDS,
        }[section]
        field, _, value = line.strip().partition(":")
        if field not in allowed:
            raise GoldenFormatError(f"{path}:{number}: unknown field {field!r}")
        current[field] = _parse_scalar(value)

    parsed: list[GoldenQuestion] = []
    for row in rows:
        missing = [field for field in FIELDS if field not in row]
        if missing:
            raise GoldenFormatError(f"{path}: row {row.get('topic')!r} is missing {missing}")
        if not isinstance(row["page"], int) or isinstance(row["page"], bool):
            raise GoldenFormatError(f"{path}: row {row['topic']!r} has a non-integer page")
        parsed.append(
            GoldenQuestion(
                question=str(row["question"]),
                page=int(row["page"]),
                topic=str(row["topic"]),
                evidence=str(row["evidence"]),
                verified_by=str(row["verified_by"]),
            )
        )
    if off_topic is None:
        raise GoldenFormatError(f"{path}: the off_topic block is missing")
    missing = [field for field in OFF_TOPIC_FIELDS if field not in off_topic]
    if missing:
        raise GoldenFormatError(f"{path}: off_topic is missing {missing}")
    return GoldenSet(
        questions=tuple(parsed),
        off_topic_question=str(off_topic["question"]),
        off_topic_reason=str(off_topic["reason_expected"]),
        filter_cases=tuple(_filter_case(path, row) for row in filter_rows),
        locality_cases=tuple(_locality_case(path, row) for row in locality_rows),
        refusal_cases=tuple(_refusal_case(path, row) for row in refusal_rows),
    )


def _filter_case(path, row: dict[str, object]) -> GoldenFilterCase:  # type: ignore[no-untyped-def]
    missing = [field for field in FILTER_FIELDS if field not in row]
    if missing:
        raise GoldenFormatError(f"{path}: filter case {row.get('question')!r} is missing {missing}")
    filters = row["filters"]
    if not isinstance(filters, dict):
        raise GoldenFormatError(f"{path}: filter case {row['question']!r} has non-object filters")
    total = row["total"]
    first = row["first"]
    if not isinstance(total, int) or isinstance(total, bool):
        raise GoldenFormatError(f"{path}: filter case {row['question']!r} has a non-integer total")
    if not isinstance(first, list) or not all(isinstance(name, str) for name in first):
        raise GoldenFormatError(f"{path}: filter case {row['question']!r} has a bad first list")
    return GoldenFilterCase(
        question=str(row["question"]),
        filters=_provider_filters_of(path, row, filters),
        total=total,
        first=tuple(str(name) for name in first),
    )


def _provider_filters_of(  # type: ignore[no-untyped-def]
    path, row: dict[str, object], filters: dict[str, object]
) -> ProviderFilters:
    """The ``ProviderFilters`` a correct intent parse must produce for one case row."""
    state = filters.get("state")
    if state is not None and not isinstance(state, str):
        raise GoldenFormatError(f"{path}: case {row.get('question')!r} has a non-string state")
    return ProviderFilters(
        state=state,
        postcode=filters.get("postcode"),
        locality=filters.get("locality"),
        group=filters.get("group"),
        status=filters.get("status"),
        brisbane_area=filters.get("brisbane_area"),
    )


def _locality_case(path, row: dict[str, object]) -> GoldenLocalityCase:  # type: ignore[no-untyped-def]
    missing = [field for field in ("question", "filters", "outcome") if field not in row]
    if missing:
        raise GoldenFormatError(
            f"{path}: locality case {row.get('question')!r} is missing {missing}"
        )
    filters = row["filters"]
    if not isinstance(filters, dict):
        raise GoldenFormatError(f"{path}: locality case {row['question']!r} has non-object filters")
    outcome = str(row["outcome"])
    if outcome not in ("resolved", "locality_unknown", "locality_ambiguous"):
        raise GoldenFormatError(
            f"{path}: locality case {row['question']!r} has outcome {outcome!r}"
        )
    total = row.get("total")
    first = row.get("first", [])
    candidates = row.get("candidates", [])
    if outcome == "resolved":
        if not isinstance(total, int) or isinstance(total, bool):
            raise GoldenFormatError(
                f"{path}: resolved locality case {row['question']!r} needs an integer total"
            )
        if not isinstance(first, list) or not all(isinstance(name, str) for name in first):
            raise GoldenFormatError(
                f"{path}: resolved locality case {row['question']!r} has a bad first list"
            )
        if candidates:
            raise GoldenFormatError(
                f"{path}: resolved locality case {row['question']!r} must not list candidates"
            )
    elif total is not None or first:
        raise GoldenFormatError(
            f"{path}: {outcome} locality case {row['question']!r} must not expect results"
        )
    if outcome == "locality_ambiguous":
        if not isinstance(candidates, list) or not all(
            isinstance(pair, str) and len(pair.split()) == 2 for pair in candidates
        ):
            raise GoldenFormatError(
                f"{path}: ambiguous locality case {row['question']!r} "
                "needs 'STATE POSTCODE' candidates"
            )
    elif candidates:
        raise GoldenFormatError(
            f"{path}: {outcome} locality case {row['question']!r} must not list candidates"
        )
    return GoldenLocalityCase(
        question=str(row["question"]),
        filters=_provider_filters_of(path, row, filters),
        outcome=outcome,
        total=total if isinstance(total, int) and not isinstance(total, bool) else None,
        first=tuple(str(name) for name in first) if isinstance(first, list) else (),
        candidates=tuple(tuple(str(pair).split()) for pair in candidates if isinstance(pair, str)),
    )


def _refusal_case(path, row: dict[str, object]) -> GoldenRefusalCase:  # type: ignore[no-untyped-def]
    missing = [field for field in REFUSAL_FIELDS if field not in row]
    if missing:
        raise GoldenFormatError(
            f"{path}: refusal case {row.get('question')!r} is missing {missing}"
        )
    adversarial = row["adversarial"]
    if not isinstance(adversarial, bool):
        raise GoldenFormatError(
            f"{path}: refusal case {row['question']!r} has a non-boolean adversarial"
        )
    return GoldenRefusalCase(
        question=str(row["question"]),
        expected_reason=str(row["expected_reason"]),
        adversarial=adversarial,
    )


def golden_pairs(sites: Iterable[GoldenQuestion]) -> tuple[tuple[str, int], ...]:
    """The (question, expected page) pairs the retrieval pass embeds and searches."""
    return tuple((site.question, site.page) for site in sites)


@dataclass(frozen=True)
class CachedIntent:
    """One question's intent payload as the configured model answered it, captured on a
    paced real run so evals can replay the parse without spending quota."""

    question: str
    payload: str


def read_intent_cache(path) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """The captured payloads keyed by question; raises ``GoldenFormatError`` on an
    unknown field, a bad shape, or a duplicate question."""
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    in_section = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "intents:":
            in_section = True
            continue
        if not in_section:
            raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        if line.startswith("- "):
            current = {}
            rows.append(current)
            line = "  " + line[2:]
        if current is None or not line.startswith("  ") or ":" not in line:
            raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        field, _, value = line.strip().partition(":")
        if field not in INTENT_FIELDS:
            raise GoldenFormatError(f"{path}:{number}: unknown field {field!r}")
        current[field] = _parse_scalar(value)
    cached = []
    for number, row in enumerate(rows, start=2):
        missing = [field for field in INTENT_FIELDS if field not in row]
        if missing:
            raise GoldenFormatError(f"{path}: cache row {number} is missing {missing}")
        cached.append(CachedIntent(str(row["question"]), str(row["payload"])))
    return {entry.question: entry.payload for entry in cached}
