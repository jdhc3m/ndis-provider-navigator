"""Pure rules for parsing one natural-language question into an ``Intent``, and for the
refusals that come before any execution.

The model's JSON is untrusted input, exactly as in ticket 05: the parser is total (it
never raises on model output) and every part it parses is validated against fixed
vocabularies. Anything it cannot understand is dropped into machine-readable rejection
reasons, so a malformed or adversarial question can only ever produce a fail-safe
response — never a crash and never a half-understood execution.
"""

import json
from dataclasses import dataclass

from ndis.domain.entities import Remoteness
from ndis.domain.register import REGISTRATION_STATUSES, STATES

# A rule question echoed by the model longer than this is not the user's question (the
# HTTP layer caps questions at the same length); longer text is dropped.
MAX_RULE_QUESTION_CHARS = 500
MAX_GROUP_CHARS = 120
MAX_ITEM_CHARS = 60

REFUSAL_DECISION_REQUEST = "decision_request"
REFUSAL_INTENT_UNPARSEABLE = "intent_unparseable"
REFUSAL_EMPTY_INTENT = "empty_intent"

DECISION_REFUSAL_EXPLANATION = (
    "This system does not make recommendations and does not make funding decisions. "
    "It reports provider-register facts, published price limits and the NDIS pricing "
    "arrangements, and leaves every choice to you."
)
EMPTY_INTENT_EXPLANATION = (
    "The question could not be mapped to anything this system answers: provider-register "
    "filters, a support-item price limit, or the NDIS pricing rules. Try naming a state, "
    "postcode or registration group, a support item number, or a question about the "
    "pricing arrangements."
)
INTENT_UNPARSEABLE_EXPLANATION = (
    "The question could not be understood as a structured request. Try again in plainer "
    "words: name a state, postcode or registration group, a support item number, or ask "
    "about the NDIS pricing arrangements."
)


@dataclass(frozen=True)
class ProviderFilters:
    """Register filters parsed from a question; every part is ``None`` when unstated."""

    state: str | None = None
    postcode: str | None = None
    group: str | None = None
    status: str | None = None
    brisbane_area: bool | None = None

    def stated(self) -> bool:
        """False when no filter is actually set, so an all-null object is no query."""
        return any(
            value is not None
            for value in (self.state, self.postcode, self.group, self.status, self.brisbane_area)
        )


@dataclass(frozen=True)
class PriceQuery:
    """One support item priced for one state; ``remoteness`` is ``None`` when unstated."""

    item: str
    state: str
    remoteness: str | None = None


@dataclass(frozen=True)
class Intent:
    """What the question asked for. Every part is optional; an intent with nothing set
    and ``decision_request`` false is empty and nothing is executed."""

    provider_filters: ProviderFilters | None = None
    price_query: PriceQuery | None = None
    rule_question: str | None = None
    decision_request: bool = False

    def empty(self) -> bool:
        return (
            self.provider_filters is None
            and self.price_query is None
            and self.rule_question is None
            and not self.decision_request
        )


@dataclass(frozen=True)
class ParsedIntent:
    """What survived parsing the model's payload; ``rejected`` holds short machine
    reasons for every dropped part. ``intent`` is ``None`` only for payloads that are not
    a JSON object at all."""

    intent: Intent | None
    rejected: tuple[str, ...] = ()


def _valid_postcode(value: str) -> bool:
    return value.isdigit() and len(value) == 4


def _canonical_status(value: str) -> str | None:
    known = {status.lower(): status for status in REGISTRATION_STATUSES}
    return known.get(value.strip().lower())


def _provider_filters(document: object, rejected: list[str]) -> ProviderFilters | None:
    """The parsed filters, or ``None``. One unusable field drops the whole filter set:
    half-understood filters must never execute."""
    if document is None:
        return None
    if not isinstance(document, dict):
        rejected.append("provider_filters_not_object")
        return None
    state = document.get("state")
    if state is not None and (not isinstance(state, str) or state.strip().upper() not in STATES):
        rejected.append("provider_filters_invalid")
        return None
    postcode = document.get("postcode")
    if postcode is not None and (
        not isinstance(postcode, str) or not _valid_postcode(postcode.strip())
    ):
        rejected.append("provider_filters_invalid")
        return None
    group = document.get("group")
    if group is not None and (
        not isinstance(group, str) or not 0 < len(group.strip()) <= MAX_GROUP_CHARS
    ):
        rejected.append("provider_filters_invalid")
        return None
    status = document.get("status")
    if status is not None and (not isinstance(status, str) or _canonical_status(status) is None):
        rejected.append("provider_filters_invalid")
        return None
    brisbane_area = document.get("brisbane_area")
    if brisbane_area is not None and not isinstance(brisbane_area, bool):
        rejected.append("provider_filters_invalid")
        return None
    filters = ProviderFilters(
        state=state.strip().upper() if isinstance(state, str) else None,
        postcode=postcode.strip() if isinstance(postcode, str) else None,
        group=group.strip() if isinstance(group, str) else None,
        status=_canonical_status(status) if isinstance(status, str) else None,
        brisbane_area=brisbane_area,
    )
    if not filters.stated():
        rejected.append("provider_filters_empty")
        return None
    return filters


def _price_query(document: object, rejected: list[str]) -> PriceQuery | None:
    """The parsed price query, or ``None``; one unusable field drops the whole query."""
    if document is None:
        return None
    if not isinstance(document, dict):
        rejected.append("price_query_not_object")
        return None
    item = document.get("item")
    if item is not None and (
        not isinstance(item, str) or not 0 < len(item.strip()) <= MAX_ITEM_CHARS
    ):
        rejected.append("price_query_invalid")
        return None
    state = document.get("state")
    if state is not None and (not isinstance(state, str) or state.strip().upper() not in STATES):
        rejected.append("price_query_invalid")
        return None
    remoteness = document.get("remoteness")
    if remoteness is not None:
        if not isinstance(remoteness, str) or remoteness.strip().lower() not in {
            tier.value for tier in Remoteness
        }:
            rejected.append("price_query_invalid")
            return None
    if item is None:
        rejected.append("price_query_empty")
        return None
    if state is None:
        # The price-limit lookup needs a state; one was not stated, so nothing is executed.
        rejected.append("price_query_no_state")
        return None
    return PriceQuery(
        item=item.strip(),
        state=state.strip().upper(),
        remoteness=remoteness.strip().lower() if isinstance(remoteness, str) else None,
    )


def _rule_question(value: object, rejected: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        rejected.append("rule_question_not_string")
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > MAX_RULE_QUESTION_CHARS:
        rejected.append("rule_question_too_long")
        return None
    return text


def parse_intent(payload: str) -> ParsedIntent:
    """Parse the model's intent JSON. Total: any structural surprise becomes a rejection
    reason, never an exception, so a bad model answer can only lose its own data.

    Field values are validated against the register's states and statuses, the four-digit
    postcode shape and the remoteness tiers; an unusable field drops its whole part so
    nothing half-understood is executed."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return ParsedIntent(None, ("not_json",))
    if not isinstance(document, dict):
        return ParsedIntent(None, ("not_object",))

    rejected: list[str] = []
    decision_request = document.get("decision_request")
    if decision_request is not None and not isinstance(decision_request, bool):
        rejected.append("decision_request_not_bool")
        decision_request = False
    return ParsedIntent(
        Intent(
            provider_filters=_provider_filters(document.get("provider_filters"), rejected),
            price_query=_price_query(document.get("price_query"), rejected),
            rule_question=_rule_question(document.get("rule_question"), rejected),
            decision_request=bool(decision_request),
        ),
        tuple(rejected),
    )
