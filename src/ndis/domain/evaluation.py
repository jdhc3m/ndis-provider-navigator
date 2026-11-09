"""Pure rules for the hand-checked entity-resolution sample: drawing it and scoring it."""

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ndis.domain.entity_resolution import MatchStatus, ProviderLink

VERDICT_CORRECT = "correct"
VERDICT_INCORRECT = "incorrect"
VERDICT_UNSURE = "unsure"
VERDICTS = (VERDICT_CORRECT, VERDICT_INCORRECT, VERDICT_UNSURE)


class InvalidVerdictError(ValueError):
    """A verdict outside ``VERDICTS``; raised so a typo cannot silently count as unlabelled."""


@dataclass(frozen=True)
class PrecisionResult:
    """Precision over the decided labels; ``precision`` is ``None`` until any row is decided.

    ``labelled`` counts ``correct`` plus ``incorrect``; ``unsure`` rows are reported but take
    no part in the ratio, and blank rows are simply ``total - labelled - unsure``.
    """

    total: int
    labelled: int
    correct: int
    unsure: int
    precision: float | None

    def summary(self) -> str:
        if self.precision is None:
            return f"{self.labelled} of {self.total} verdicts filled; no precision yet"
        return (
            f"{self.labelled} of {self.total} verdicts filled ({self.unsure} unsure); "
            f"precision {self.precision:.3f} ({self.correct}/{self.labelled})"
        )


def precision_from_verdicts(verdicts: Iterable[str | None]) -> PrecisionResult:
    """Precision of the matcher on the rows a person has marked ``correct`` or ``incorrect``."""
    total = labelled = correct = unsure = 0
    for verdict in verdicts:
        total += 1
        if verdict is None:
            continue
        if verdict not in VERDICTS:
            raise InvalidVerdictError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
        if verdict == VERDICT_UNSURE:
            unsure += 1
            continue
        labelled += 1
        correct += verdict == VERDICT_CORRECT
    precision = None if labelled == 0 else correct / labelled
    return PrecisionResult(total, labelled, correct, unsure, precision)


def stratified_sample(
    links: Sequence[ProviderLink], size: int, seed: int
) -> tuple[ProviderLink, ...]:
    """Draw ``size`` links deterministically with every present status represented.

    Each status gets one row first, the rest are spread in proportion to the status counts
    (largest remainders fill the gaps), and rows within a status are chosen by ``seed`` after
    sorting by place id so the draw does not depend on input order.
    """
    by_status: dict[MatchStatus, list[ProviderLink]] = {}
    for link in sorted(links, key=lambda link: link.place.place_id):
        by_status.setdefault(link.match.match_status, []).append(link)
    if not by_status or size <= 0:
        return ()
    size = min(size, len(links))
    quotas = {status: min(1, len(rows)) for status, rows in by_status.items()}
    remaining = size - sum(quotas.values())
    if remaining > 0:
        shares = {status: remaining * len(rows) / len(links) for status, rows in by_status.items()}
        for status, share in shares.items():
            quotas[status] = min(quotas[status] + int(share), len(by_status[status]))
        leftovers = sorted(by_status, key=lambda status: (-(shares[status] % 1), status.value))
        while sum(quotas.values()) < size:
            for status in leftovers:
                if quotas[status] < len(by_status[status]) and sum(quotas.values()) < size:
                    quotas[status] += 1
    rng = random.Random(seed)
    chosen: list[ProviderLink] = []
    for status in sorted(by_status, key=lambda status: status.value):
        chosen.extend(rng.sample(by_status[status], quotas[status]))
    return tuple(chosen)
