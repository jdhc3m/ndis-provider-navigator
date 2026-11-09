"""Entity-resolution precision on the hand-checked sample in ``er_sample.yaml``.

No model is involved; the eval marker keeps it out of the unit run because the result depends
on a person filling in verdicts. Until any row is labelled the test skips with a visible
message instead of passing on nothing.
"""

from pathlib import Path

import pytest

from ndis.adapters.er_sample import read_sample
from ndis.domain.evaluation import precision_from_verdicts

SAMPLE = Path(__file__).with_name("er_sample.yaml")
# Provisional floor for the exact (domain/name) and fuzzy rules together; ticket 08 replaces
# it with a measured threshold once the owner has labelled the sample.
MINIMUM_PRECISION = 0.9


@pytest.mark.eval
def test_entity_resolution_precision_on_labelled_rows() -> None:
    rows = read_sample(SAMPLE)
    result = precision_from_verdicts(row.verdict for row in rows)
    print(f"entity resolution: {result.summary()}")
    if result.precision is None:
        pytest.skip(f"{result.labelled} of {result.total} verdicts filled in {SAMPLE.name}")
    assert result.precision >= MINIMUM_PRECISION, result.summary()
