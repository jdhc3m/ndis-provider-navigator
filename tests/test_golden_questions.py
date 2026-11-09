"""The golden-file reader on a written fixture and on the committed file's shape."""

from pathlib import Path

import pytest

from ndis.adapters.golden_questions import GoldenFormatError, golden_pairs, read_golden


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "golden.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_read_golden_round_trip(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "# header comment\n"
        "questions:\n"
        '- question: "Can a provider charge for travel?"\n'
        "  page: 22\n"
        "  topic: provider_travel\n"
        '  evidence: "Provider Travel - Labour Costs (Time)"\n'
        "  verified_by: agent\n"
        "off_topic:\n"
        '  question: "What is the capital of France?"\n'
        "  reason_expected: below_similarity_threshold\n",
    )
    golden = read_golden(path)
    assert len(golden.questions) == 1
    assert golden.questions[0].page == 22
    assert golden.questions[0].verified_by == "agent"
    assert golden_pairs(golden.questions) == (("Can a provider charge for travel?", 22),)
    assert golden.off_topic_question == "What is the capital of France?"
    assert golden.off_topic_reason == "below_similarity_threshold"


@pytest.mark.parametrize(
    "body",
    [
        'questions:\n- question: "x"\n  page: not_a_number\n  topic: t\n'
        '  evidence: "e"\n  verified_by: agent\noff_topic:\n  question: "y"\n'
        "  reason_expected: r\n",
        'questions:\n- question: "x"\n  page: 1\n  topic: t\n  evidence: "e"\n'
        'off_topic:\n  question: "y"\n  reason_expected: r\n',
        'questions:\n- question: "x"\n  page: 1\n  topic: t\n  evidence: "e"\n'
        '  verified_by: agent\n  extra: 1\noff_topic:\n  question: "y"\n'
        "  reason_expected: r\n",
        'questions:\n- question: "x"\n  page: 1\n  topic: t\n  evidence: "e"\n'
        "  verified_by: agent\n",
    ],
)
def test_read_golden_rejects_bad_shapes(tmp_path: Path, body: str) -> None:
    with pytest.raises(GoldenFormatError):
        read_golden(write(tmp_path, body))


def test_committed_golden_file_parses() -> None:
    golden = read_golden(Path(__file__).resolve().parents[1] / "evals" / "golden.yaml")
    assert len(golden.questions) == 10
    assert all(site.verified_by == "agent" for site in golden.questions)
    assert len({site.topic for site in golden.questions}) >= 8  # two cancellation questions
