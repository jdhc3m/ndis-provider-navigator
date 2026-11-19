"""Pure locality rules: the gazetteer row of the Australian postcodes CSV, name
normalisation and the three-outcome resolver.

The locality (suburb) name is looked up in code, never by the model: the model copies the
name out of the question and the resolver turns it into a ``(state, postcode)`` pair.
Ambiguity is a first-class result instead of a guess — a name that maps to several
postcodes is reported with its candidates, and an unknown name is reported rather than
dropped. Names that differ only by case, spacing, hyphens or apostrophes are candidates
of each other, so "South Bank" and "Southbank" share one lookup key."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ndis.domain.geo import COLUMN_POSTCODE, NON_GEOGRAPHIC_POSTCODES
from ndis.domain.register import STATES, is_brisbane_postcode

COLUMN_LOCALITY = "locality"
COLUMN_STATE = "state"
COLUMN_TYPE = "type"

# Only these rows are places; the same CSV's post-office-box and large-volume-receiver
# rows name post offices and mail routes, not suburbs people ask for.
DELIVERY_AREA_TYPE = "Delivery Area"

# A locality name longer than this is not a suburb (the HTTP layer caps questions far
# below it); the intent parser drops the whole filter set for one.
MAX_LOCALITY_CHARS = 80

REJECTION_LOCALITY_POSTCODE_MISMATCH = "locality_postcode_mismatch"


class LocalityRowError(ValueError):
    """A postcode CSV row that cannot become a gazetteer locality; ``reason`` is a slug."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Locality:
    """One gazetteer row: the locality name as the source spells it, its state and the
    postcode it belongs to."""

    name: str
    state: str
    postcode: str


def normalise_locality(name: str) -> str:
    """The lookup key for a locality name: casefolded with every non-alphanumeric
    character removed, so names differing only by case, spacing, hyphens or apostrophes
    ("South Bank", "Southbank", "ST LUCIA", "O'Connor") collide into one key."""
    return re.sub(r"[^0-9a-z]", "", name.casefold())


def parse_locality_row(row: Mapping[str, str | None]) -> Locality:
    """One ``Locality`` from a row of the Australian postcodes CSV.

    Only ``Delivery Area`` rows are places: a row of any other type (post-office boxes,
    large-volume receivers, unspecified) is ``not_delivery_area``. A missing or blank
    name is ``missing_locality``; a state outside the register's states is
    ``invalid_state``; a postcode that is not four digits is ``invalid_postcode``; a
    postcode in ``NON_GEOGRAPHIC_POSTCODES`` (9999 "NORTH POLE", typed as an ordinary
    Delivery Area by the source) is ``non_geographic`` even though the row is otherwise
    well formed; a name with no alphanumeric characters is ``invalid_locality``.
    """
    if (row.get(COLUMN_TYPE) or "").strip() != DELIVERY_AREA_TYPE:
        raise LocalityRowError("not_delivery_area")
    name = (row.get(COLUMN_LOCALITY) or "").strip()
    if not name:
        raise LocalityRowError("missing_locality")
    state = (row.get(COLUMN_STATE) or "").strip().upper()
    if state not in STATES:
        raise LocalityRowError("invalid_state")
    postcode = (row.get(COLUMN_POSTCODE) or "").strip()
    if not (postcode.isdigit() and len(postcode) == 4):
        raise LocalityRowError("invalid_postcode")
    if postcode in NON_GEOGRAPHIC_POSTCODES:
        raise LocalityRowError("non_geographic")
    if not normalise_locality(name):
        raise LocalityRowError("invalid_locality")
    return Locality(name=name, state=state, postcode=postcode)


class LocalityOutcome(StrEnum):
    """What the resolver made of a locality name."""

    RESOLVED = "resolved"
    """Exactly one (state, postcode) matched."""
    UNKNOWN = "locality_unknown"
    """No gazetteer name matches (or none survives the state/Brisbane narrowing)."""
    AMBIGUOUS = "locality_ambiguous"
    """Several (state, postcode) pairs match; ``candidates`` lists them all."""


@dataclass(frozen=True)
class LocalityResolution:
    """The outcome of resolving one locality name. ``locality`` is set only when
    ``outcome`` is ``resolved``; ``candidates`` only when it is ``ambiguous``."""

    outcome: LocalityOutcome
    name: str
    """The name exactly as the question wrote it."""
    locality: Locality | None = None
    candidates: tuple[Locality, ...] = ()


def resolve_locality(
    name: str,
    candidates: Sequence[Locality],
    *,
    state: str | None = None,
    brisbane_area: bool | None = None,
) -> LocalityResolution:
    """Resolve ``name`` against the gazetteer rows whose lookup key equals the name's.

    The pool is narrowed by a stated state (exact match) and by ``brisbane_area: true``
    (the candidate's postcode must be in the Brisbane-area ranges; ``False`` narrows
    nothing). One distinct ``(state, postcode)`` pair resolves; several are ambiguous
    with the full candidate list; none is unknown. Rows that share a pair — different
    spellings of the same place — collapse into one candidate, preferring the spelling
    the question itself used.
    """
    pool = list(candidates)
    if state is not None:
        pool = [candidate for candidate in pool if candidate.state == state]
    if brisbane_area:
        pool = [candidate for candidate in pool if is_brisbane_postcode(candidate.postcode)]
    distinct: dict[tuple[str, str], Locality] = {}
    for candidate in sorted(pool, key=lambda item: (item.state, item.postcode, item.name)):
        key = (candidate.state, candidate.postcode)
        kept = distinct.get(key)
        if kept is None or (
            kept.name.casefold() != name.casefold() and candidate.name.casefold() == name.casefold()
        ):
            distinct[key] = candidate
    if not distinct:
        return LocalityResolution(LocalityOutcome.UNKNOWN, name)
    if len(distinct) == 1:
        return LocalityResolution(LocalityOutcome.RESOLVED, name, next(iter(distinct.values())))
    return LocalityResolution(LocalityOutcome.AMBIGUOUS, name, candidates=tuple(distinct.values()))
