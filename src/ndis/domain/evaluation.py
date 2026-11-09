"""Pure rules for the hand-checked entity-resolution sample: drawing it and scoring it."""

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ndis.domain.entity_resolution import MatchStatus, MatchStrategy, ProviderLink

type Stratum = tuple[MatchStatus, MatchStrategy]

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
    """Draw ``size`` links deterministically with every present (status, strategy) represented.

    Each stratum gets one row first, so the least-trusted rules (fuzzy, a tie-broken domain)
    always reach the hand-check; the rest are spread in proportion to the stratum sizes
    (largest remainders fill the gaps), and rows within a stratum are chosen by ``seed`` after
    sorting by place id so the draw does not depend on input order.
    """
    strata: dict[Stratum, list[ProviderLink]] = {}
    for link in sorted(links, key=lambda link: link.place.place_id):
        strata.setdefault((link.match.match_status, link.match.strategy), []).append(link)
    if not strata or size <= 0:
        return ()
    size = min(size, len(links))
    quotas = {stratum: min(1, len(rows)) for stratum, rows in strata.items()}
    remaining = size - sum(quotas.values())
    if remaining > 0:
        shares = {stratum: remaining * len(rows) / len(links) for stratum, rows in strata.items()}
        for stratum, share in shares.items():
            quotas[stratum] = min(quotas[stratum] + int(share), len(strata[stratum]))
        leftovers = sorted(strata, key=lambda s: (-(shares[s] % 1), s[0].value, s[1].value))
        while sum(quotas.values()) < size:
            for stratum in leftovers:
                if quotas[stratum] < len(strata[stratum]) and sum(quotas.values()) < size:
                    quotas[stratum] += 1
    rng = random.Random(seed)
    chosen: list[ProviderLink] = []
    for stratum in sorted(strata, key=lambda s: (s[0].value, s[1].value)):
        chosen.extend(rng.sample(strata[stratum], quotas[stratum]))
    return tuple(chosen)
