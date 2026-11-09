"""Read ``evals/thresholds.yaml``: the quality floors the eval gate enforces.

The file is the same small, fixed YAML subset the other eval files use — a comment header
and one ``key: <number>`` line per floor — parsed here without a YAML dependency. Every
key must be present exactly once and carry a fraction between 0 and 1, so a typo or a
deleted floor fails loudly instead of silently unenforcing a gate.
"""

import json
from dataclasses import dataclass

FLOORS = ("filter_accuracy", "rule_hit_at_3", "extraction_precision", "refusal_recall")


class ThresholdFormatError(ValueError):
    """The thresholds file does not follow the layout this module reads."""


@dataclass(frozen=True)
class Thresholds:
    """The four eval-gate floors, each a fraction of the metric's denominator."""

    filter_accuracy: float
    rule_hit_at_3: float
    extraction_precision: float
    refusal_recall: float


def read_thresholds(path) -> Thresholds:  # type: ignore[no-untyped-def]
    """Parse the file; raises ``ThresholdFormatError`` on an unknown key, a missing floor,
    a duplicate, or a value outside [0, 1]."""
    values: dict[str, float] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ThresholdFormatError(f"{path}:{number}: unexpected line {raw!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in FLOORS:
            raise ThresholdFormatError(f"{path}:{number}: unknown floor {key!r}")
        if key in values:
            raise ThresholdFormatError(f"{path}:{number}: duplicate floor {key!r}")
        try:
            parsed = json.loads(value.strip())
        except json.JSONDecodeError as exc:
            raise ThresholdFormatError(
                f"{path}:{number}: {value.strip()!r} is not a number"
            ) from exc
        if not isinstance(parsed, int | float) or isinstance(parsed, bool) or not 0 <= parsed <= 1:
            raise ThresholdFormatError(
                f"{path}:{number}: {value.strip()!r} is not a fraction between 0 and 1"
            )
        values[key] = float(parsed)
    missing = [key for key in FLOORS if key not in values]
    if missing:
        raise ThresholdFormatError(f"{path}: missing floors {missing}")
    return Thresholds(**values)
