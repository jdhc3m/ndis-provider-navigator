"""Read ``evals/golden.yaml``: the ten pricing-rule questions with source-verified expected
pages, plus the off-topic refusal case.

The file is the same small, fixed YAML subset the other eval files use: a comment header,
one top-level list key (``questions``) of block mappings whose string values are JSON
quoted, and one ``off_topic`` block. ``page`` is the printed page of the pricing PDF the
answer's evidence lives on, verified against the actual document.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass

FIELDS = ("question", "page", "topic", "evidence", "verified_by")
OFF_TOPIC_FIELDS = ("question", "reason_expected")


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
class GoldenSet:
    """The whole golden file: the questions and the off-topic refusal case."""

    questions: tuple[GoldenQuestion, ...]
    off_topic_question: str
    off_topic_reason: str


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
    off_topic: dict[str, object] | None = None
    in_off_topic = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "questions:":
            continue
        if line == "off_topic:":
            in_off_topic = True
            off_topic = {}
            continue
        if in_off_topic and off_topic is not None:
            if not line.startswith("  ") or ":" not in line:
                raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
            field, _, value = line.strip().partition(":")
            if field not in OFF_TOPIC_FIELDS:
                raise GoldenFormatError(f"{path}:{number}: unknown field {field!r}")
            off_topic[field] = _parse_scalar(value)
            continue
        if line.startswith("- "):
            current: dict[str, object] = {}
            rows.append(current)
            line = "  " + line[2:]
        if not line.startswith("  ") or ":" not in line:
            raise GoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        field, _, value = line.strip().partition(":")
        if field not in FIELDS:
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
    )


def golden_pairs(sites: Iterable[GoldenQuestion]) -> tuple[tuple[str, int], ...]:
    """The (question, expected page) pairs the retrieval pass embeds and searches."""
    return tuple((site.question, site.page) for site in sites)
