"""Pure service-name search over the support catalogue: a service phrase in, matching
support items out.

A person knows the service ("occupational therapy", "OT", "support coordination"), not
the support item number, and the catalogue names its items ("Therapy - Occupational
Therapist - Direct Service"), so a substring match on the question's words finds nothing.
The mapping is code, never the model: the phrase is normalised deterministically, a small
explicit alias table translates the words people use into the words the catalogue uses,
and an item matches when its name contains the phrase's token sequence in order. One name
means one price in the 2026-27 catalogue — no shared name carries two different national
limits — so every match of a phrase is priced from its own row.

The alias table is pinned by tests (``tests/test_domain_service_search.py``): adding,
removing or rewording an alias must fail a test first, because the mapping is part of the
product's contract with the user, not an implementation detail.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from ndis.domain.entities import SupportItem

# One answer lists at most this many matching items (with the total stated), so one broad
# phrase cannot flood the price column — the same idea as MAX_SERVICE_MENTIONS.
MAX_SHOWN_SERVICE_MATCHES = 12

# A service phrase longer than this is not a service name (the intent parser caps it at
# the same length; the HTTP layer never sees a longer one).
MAX_SERVICE_PHRASE_CHARS = 80

# What people call a service -> the tokens the catalogue's item names actually use.
# Multi-word phrases first, then single tokens; every entry is pinned by a test.
PHRASE_ALIASES: dict[str, tuple[str, ...]] = {
    "occupational therapy": ("occupational", "therapist"),
    "speech therapy": ("speech", "pathologist"),
    "speech pathology": ("speech", "pathologist"),
    "physiotherapy": ("physiotherapist",),
    "physical therapy": ("physiotherapist",),
    "psychology": ("psychologist",),
    "support coordinator": ("support", "coordination"),
    "plan manager": ("plan", "management"),
}
TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "ot": ("occupational", "therapist"),
    "physio": ("physiotherapist",),
    "psych": ("psychologist",),
}


def normalise_service_phrase(phrase: str) -> str:
    """Lower-case, every non-alphanumeric character becomes a space, whitespace collapsed
    (``OT`` -> ``ot``, ``Speech  Pathologist`` -> ``speech pathologist``)."""
    characters = [character if character.isalnum() else " " for character in phrase.lower()]
    return " ".join("".join(characters).split())


def service_search_tokens(phrase: str) -> tuple[str, ...]:
    """The tokens an item name must contain, in order, for the phrase to match it.

    The whole normalised phrase is looked up in ``PHRASE_ALIASES`` first (a multi-word
    alias such as "speech therapy" would not survive token-by-token translation), then
    single tokens are expanded through ``TOKEN_ALIASES``. Duplicates keep their first
    position, so the token order — and therefore the matching — is stable.
    """
    text = normalise_service_phrase(phrase)
    if not text:
        return ()
    if text in PHRASE_ALIASES:
        return PHRASE_ALIASES[text]
    tokens: list[str] = []
    for token in text.split():
        tokens.extend(TOKEN_ALIASES.get(token, (token,)))
    return tuple(dict.fromkeys(tokens))


def is_claim_type_variant(item_number: str) -> bool:
    """True when the item number ends in a letter suffix (``_PT``, ``_CA2``): a
    claim-type variant of a base item (Provider Travel, Cancellation, ...) rather than the
    base item itself. The trailing segment of a plain number is digits."""
    tail = item_number.rsplit("_", 1)[-1]
    return tail[:1].isalpha()


def _contains_subsequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle:
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return True
    return False


def _order_key(item: SupportItem) -> tuple[bool, bool, str]:
    """Base items before claim-type variants, current before legacy, then item number."""
    return (is_claim_type_variant(item.item_number), item.legacy, item.item_number)


@dataclass(frozen=True)
class ServiceSearch:
    """The outcome of one service search: the phrase as written, and every matching item
    in the deterministic order (the caller caps what it shows)."""

    phrase: str
    matches: tuple[SupportItem, ...]

    @property
    def total(self) -> int:
        return len(self.matches)


def search_service_items(phrase: str, items: Iterable[SupportItem]) -> ServiceSearch:
    """Match ``phrase`` against item names in code: every item whose name contains the
    phrase's token sequence in order, ordered deterministically. A phrase that normalises
    to no tokens (blank or pure punctuation) matches nothing, and so does a phrase no
    catalogue name contains — both are honest empty results, never a guess."""
    tokens = service_search_tokens(phrase)
    if not tokens:
        return ServiceSearch(" ".join(phrase.split()), ())
    matches = [
        item
        for item in items
        if _contains_subsequence(tuple(normalise_service_phrase(item.name).split()), tokens)
    ]
    matches.sort(key=_order_key)
    return ServiceSearch(" ".join(phrase.split()), tuple(matches))
