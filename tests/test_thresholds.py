"""The thresholds reader: the eval-gate floors parse strictly or not at all."""

import pytest

from ndis.adapters.thresholds import ThresholdFormatError, read_thresholds

VALID = """\
# comment lines are ignored
filter_accuracy: 0.85
rule_hit_at_3: 0.7
extraction_precision: 0.8
refusal_recall: 1.0
"""


def write(tmp_path, text):  # type: ignore[no-untyped-def]
    path = tmp_path / "thresholds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_file_parses_to_all_four_floors(tmp_path) -> None:  # type: ignore[no-untyped-def]
    thresholds = read_thresholds(write(tmp_path, VALID))
    assert thresholds.filter_accuracy == 0.85
    assert thresholds.rule_hit_at_3 == 0.7
    assert thresholds.extraction_precision == 0.8
    assert thresholds.refusal_recall == 1.0


@pytest.mark.parametrize(
    "text",
    [
        VALID.replace("refusal_recall: 1.0\n", ""),  # a floor deleted
        VALID.replace("rule_hit_at_3", "rule_hit_at_4"),  # a renamed floor
        VALID.replace(
            "extraction_precision: 0.8", "extraction_precision: 0.8\nextraction_precision: 0.9"
        ),  # duplicated
        VALID.replace("filter_accuracy: 0.85", "filter_accuracy: high"),  # not a number
        VALID.replace("rule_hit_at_3: 0.7", "rule_hit_at_3: 1.5"),  # outside [0, 1]
        VALID.replace("refusal_recall: 1.0", "refusal_recall: true"),  # not a fraction
        VALID.replace("filter_accuracy: 0.85\n", "no separator here\n"),  # malformed line
    ],
)
def test_broken_files_raise(tmp_path, text) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ThresholdFormatError):
        read_thresholds(write(tmp_path, text))
