"""Read ``evals/chat_golden.yaml`` — the multi-turn golden conversations (ticket 16) —
and ``evals/chat_cache.yaml`` — the captured model payloads those conversations replay
from, written by ``scripts/capture_chat_cache.py``.

The files are the same small, fixed YAML subset the other eval files use: a comment
header, top-level sections, and block mappings whose values are JSON-quoted scalars or
inline objects — parsed here without a YAML dependency, strictly, so a typo fails
loudly instead of silently changing what the gate checks.

Each golden turn carries expectations for what the turn must produce: the register
``filters`` the final parsed intent must carry, the ``service`` (and optional
``service_state``) a price follow-up must name, a substring a rule follow-up's
``rule_question`` must contain, or the machine ``refusal`` reason the follow-up must be
contained with. A turn carries exactly one of ``service``, ``rule_contains`` or
``refusal`` — except that ``filters`` may accompany ``service``: the rewrite is
instructed to copy the place forward, so a price follow-up may legitimately answer with
the combined intent shape the /ask golden SA service case already treats as correct
(provider filters plus the price query).

The cache holds three sections: ``rewrites`` (the contextualise payloads, keyed by the
digest of exactly what the model saw — see :func:`rewrite_payload_key`), ``intents``
(the intent payloads for every standalone question, keyed by the question) and
``summaries`` (per conversation turn, the turn summary exactly as
``application.chat.contextualise_stage`` built it — :func:`ndis.application.chat.turn_summary`'s
``standalone_question``, ``intent`` and ``providers_shown`` — keyed by the turn's
standalone question, which the summary itself carries). The digest makes a prompt edit
invalidate the cache instead of silently replaying stale answers; the summaries pin the
rewrite prompts' history, so the replay can rebuild them byte-identically without
executing the earlier turns the way capture time did.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ndis.application.extraction import LlmRequest
from ndis.domain.intent import ProviderFilters

CONVERSATION_FIELDS = ("id", "turns")
TURN_FIELDS = ("message", "filters", "service", "service_state", "rule_contains", "refusal")
TURN_EXPECTATIONS = ("filters", "service", "rule_contains", "refusal")
REWRITE_FIELDS = ("message", "key", "payload")
CACHE_INTENT_FIELDS = ("question", "payload")
CACHE_SUMMARY_FIELDS = ("question", "payload")


class ChatGoldenFormatError(ValueError):
    """A chat golden or cache file does not follow the layout this module reads."""


@dataclass(frozen=True)
class GoldenTurn:
    """One golden turn: the user's message and the expectation the turn's final parsed
    intent (or its refusal) must satisfy."""

    message: str
    filters: ProviderFilters | None = None
    service: str | None = None
    service_state: str | None = None
    rule_contains: str | None = None
    refusal: str | None = None


@dataclass(frozen=True)
class GoldenConversation:
    """One golden conversation: two to three turns on one thread; turn 1 is a first
    turn (no rewrite), every later turn is a follow-up."""

    id: str
    turns: tuple[GoldenTurn, ...]


@dataclass(frozen=True)
class ChatGoldenSet:
    """The whole chat golden file."""

    conversations: tuple[GoldenConversation, ...]


@dataclass(frozen=True)
class ChatCache:
    """The captured payloads: rewrites keyed by :func:`rewrite_payload_key` (with the
    human-readable message label the file carries alongside each key), intents by the
    standalone question, and the per-turn summaries by the turn's standalone question —
    the dicts :func:`ndis.application.chat.turn_summary` builds."""

    rewrites: dict[str, str] = field(default_factory=dict)
    rewrite_messages: dict[str, str] = field(default_factory=dict)
    intents: dict[str, str] = field(default_factory=dict)
    summaries: dict[str, dict[str, Any]] = field(default_factory=dict)


def _parse_scalar(text: str) -> object:
    text = text.strip()
    if text in ("null", "~", ""):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _require(  # type: ignore[no-untyped-def]
    path, row: dict[str, object], fields: tuple[str, ...], what: str
) -> None:
    missing = [name for name in fields if name not in row]
    if missing:
        raise ChatGoldenFormatError(f"{path}: {what} is missing {missing}")


def read_chat_golden(path) -> ChatGoldenSet:  # type: ignore[no-untyped-def]
    """Parse the chat golden file: a ``conversations:`` section of two-level rows — each
    conversation (``- id:``) carries a ``turns:`` list of turn rows (``  - message:``).
    Raises ``ChatGoldenFormatError`` on anything unexpected."""
    conversations: list[GoldenConversation] = []
    conversation_id: str | None = None
    turn_rows: list[dict[str, object]] = []
    turn: dict[str, object] | None = None
    in_section = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "conversations:" and not line.startswith(" "):
            in_section = True
            continue
        if not in_section:
            raise ChatGoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        if not line.startswith(" "):
            # A new conversation row: finish the previous one, start this one.
            _finish_conversation(path, conversations, conversation_id, turn_rows)
            if not line.startswith("- "):
                raise ChatGoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
            field, _, value = line[2:].partition(":")
            if field != "id":
                raise ChatGoldenFormatError(f"{path}:{number}: unknown field {field!r}")
            conversation_id = str(_parse_scalar(value))
            turn_rows = []
            turn = None
            continue
        if line.startswith("  - "):
            if conversation_id is None:
                raise ChatGoldenFormatError(f"{path}:{number}: turn outside a conversation")
            turn = {}
            turn_rows.append(turn)
            line = "    " + line[4:]
        elif line.startswith("  ") and line.strip() == "turns:":
            continue
        if turn is None or not line.startswith("    ") or ":" not in line:
            raise ChatGoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        field, _, value = line.strip().partition(":")
        if field not in TURN_FIELDS:
            raise ChatGoldenFormatError(f"{path}:{number}: unknown field {field!r}")
        turn[field] = _parse_scalar(value)
    _finish_conversation(path, conversations, conversation_id, turn_rows)
    if not conversations:
        raise ChatGoldenFormatError(f"{path}: no conversations")
    return ChatGoldenSet(conversations=tuple(conversations))


def _finish_conversation(  # type: ignore[no-untyped-def]
    path,
    conversations: list[GoldenConversation],
    conversation_id: str | None,
    turn_rows: list[dict[str, object]],
) -> None:
    if conversation_id is None:
        return
    if not 2 <= len(turn_rows) <= 3:
        raise ChatGoldenFormatError(
            f"{path}: conversation {conversation_id!r} needs two or three turns"
        )
    conversations.append(
        GoldenConversation(
            id=conversation_id, turns=tuple(_golden_turn(path, row) for row in turn_rows)
        )
    )


def _golden_turn(  # type: ignore[no-untyped-def]
    path, row: dict[str, object]
) -> GoldenTurn:
    """One turn row: the message and a valid expectation combination — one of
    ``service``, ``rule_contains`` or ``refusal`` alone, or ``filters`` alone, or
    ``filters`` alongside ``service`` (the combined shape: the rewrite copies the place
    forward, so a price follow-up may carry both)."""
    _require(path, row, ("message",), f"turn {row.get('message')!r}")
    message = str(row["message"])
    if not message:
        raise ChatGoldenFormatError(f"{path}: a turn needs a non-empty message")
    kinds = frozenset(name for name in TURN_EXPECTATIONS if name in row)
    allowed = (
        frozenset({"filters"}),
        frozenset({"service"}),
        frozenset({"rule_contains"}),
        frozenset({"refusal"}),
        frozenset({"filters", "service"}),
    )
    if not kinds:
        raise ChatGoldenFormatError(f"{path}: turn {message!r} carries no expectation")
    if kinds not in allowed:
        raise ChatGoldenFormatError(
            f"{path}: turn {message!r} carries the impossible combination {sorted(kinds)}"
        )
    filters = row.get("filters")
    if "filters" in kinds and (not isinstance(filters, dict) or not filters):
        raise ChatGoldenFormatError(f"{path}: turn {message!r} has bad filters")
    service = row.get("service")
    if "service" in kinds and (not isinstance(service, str) or not service):
        raise ChatGoldenFormatError(f"{path}: turn {message!r} has a bad service")
    rule = row.get("rule_contains")
    if "rule_contains" in kinds and (not isinstance(rule, str) or not rule):
        raise ChatGoldenFormatError(f"{path}: turn {message!r} has a bad rule_contains")
    refusal = row.get("refusal")
    if "refusal" in kinds and (not isinstance(refusal, str) or not refusal):
        raise ChatGoldenFormatError(f"{path}: turn {message!r} has a bad refusal")
    if "service_state" in row and "service" not in kinds:
        raise ChatGoldenFormatError(f"{path}: turn {message!r} states a state without a service")
    state = row.get("service_state")
    if state is not None and not isinstance(state, str):
        raise ChatGoldenFormatError(f"{path}: turn {message!r} has a non-string service_state")
    return GoldenTurn(
        message=message,
        filters=None if filters is None else _provider_filters(path, row, filters),
        service=service if isinstance(service, str) else None,
        service_state=state if isinstance(state, str) else None,
        rule_contains=rule if isinstance(rule, str) else None,
        refusal=refusal if isinstance(refusal, str) else None,
    )


def _provider_filters(  # type: ignore[no-untyped-def]
    path, row: dict[str, object], filters: dict[str, object]
) -> ProviderFilters:
    """The ``ProviderFilters`` one turn's expectation names; unknown keys are a typo and
    fail loudly instead of silently passing."""
    known = {"state", "postcode", "locality", "group", "status", "brisbane_area"}
    unknown = set(filters) - known
    if unknown:
        raise ChatGoldenFormatError(
            f"{path}: turn {row.get('message')!r} has unknown filter keys {sorted(unknown)}"
        )
    state = filters.get("state")
    if state is not None and not isinstance(state, str):
        raise ChatGoldenFormatError(f"{path}: turn {row.get('message')!r} has a non-string state")
    return ProviderFilters(
        state=state,
        postcode=filters.get("postcode"),
        locality=filters.get("locality"),
        group=filters.get("group"),
        status=filters.get("status"),
        brisbane_area=filters.get("brisbane_area"),
    )


def rewrite_payload_key(request: LlmRequest) -> str:
    """The cache key for one contextualise request: a digest over the system prompt, a
    separator and the user prompt — the exact text the model saw. A prompt edit (or a
    different history behind the same message) changes the key, so the eval demands a
    fresh capture instead of replaying an answer to a different question."""
    return hashlib.sha256(f"{request.system}\x1f{request.user}".encode()).hexdigest()


def read_chat_cache(path) -> ChatCache:  # type: ignore[no-untyped-def]
    """Parse the chat cache file; duplicate keys are a capture bug and fail loudly. A
    cache written before the ``summaries`` section existed reads back with none."""
    cache = ChatCache()
    section: str | None = None
    current: dict[str, object] | None = None
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line in ("rewrites:", "intents:", "summaries:") and not line.startswith(" "):
            if current is not None:
                _cache_row(path, cache, section or "", current)
            section = line[:-1]
            current = None
            continue
        if section is None:
            raise ChatGoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        if line.startswith("- "):
            if current is not None:
                _cache_row(path, cache, section, current)
            current = {}
            line = "  " + line[2:]
        if current is None or not line.startswith("  ") or ":" not in line:
            raise ChatGoldenFormatError(f"{path}:{number}: unexpected line {raw!r}")
        field, _, value = line.strip().partition(":")
        allowed = {
            "rewrites": REWRITE_FIELDS,
            "intents": CACHE_INTENT_FIELDS,
            "summaries": CACHE_SUMMARY_FIELDS,
        }[section]
        if field not in allowed:
            raise ChatGoldenFormatError(f"{path}:{number}: unknown field {field!r}")
        current[field] = _parse_scalar(value)
    if current is not None:
        _cache_row(path, cache, section or "", current)
    return cache


def _cache_row(  # type: ignore[no-untyped-def]
    path, cache: ChatCache, section: str, row: dict[str, object]
) -> None:
    if section == "rewrites":
        _require(path, row, REWRITE_FIELDS, "a rewrite row")
        key = str(row["key"])
        if key in cache.rewrites:
            raise ChatGoldenFormatError(f"{path}: duplicate rewrite key {key}")
        cache.rewrites[key] = str(row["payload"])
        cache.rewrite_messages[key] = str(row["message"])
        return
    if section == "summaries":
        _require(path, row, CACHE_SUMMARY_FIELDS, "a summary row")
        question = str(row["question"])
        if question in cache.summaries:
            raise ChatGoldenFormatError(f"{path}: duplicate summary question {question!r}")
        cache.summaries[question] = _summary_document(path, row["payload"])
        return
    _require(path, row, CACHE_INTENT_FIELDS, "an intent row")
    question = str(row["question"])
    if question in cache.intents:
        raise ChatGoldenFormatError(f"{path}: duplicate intent question {question!r}")
    cache.intents[question] = str(row["payload"])


def _summary_document(  # type: ignore[no-untyped-def]
    path, value: object
) -> dict[str, Any]:
    """One summary row's payload: an inline JSON object of exactly the shape
    ``application.chat.turn_summary`` builds, validated so a typo fails loudly instead
    of silently feeding a different rewrite prompt."""
    if not isinstance(value, dict):
        raise ChatGoldenFormatError(f"{path}: a summary payload must be an inline JSON object")
    if set(value) != {"standalone_question", "intent", "providers_shown"}:
        raise ChatGoldenFormatError(
            f"{path}: a summary payload carries exactly standalone_question, intent "
            f"and providers_shown, not {sorted(value)}"
        )
    question = value["standalone_question"]
    providers = value["providers_shown"]
    if not isinstance(question, str):
        raise ChatGoldenFormatError(f"{path}: a summary's standalone_question must be a string")
    if not isinstance(providers, list) or not all(
        isinstance(item, dict) and set(item) == {"abn", "business_name"} for item in providers
    ):
        raise ChatGoldenFormatError(
            f"{path}: a summary's providers_shown must list abn/business_name objects"
        )
    if value["intent"] is not None and not isinstance(value["intent"], dict):
        raise ChatGoldenFormatError(f"{path}: a summary's intent must be an object or null")
    return value


def write_chat_cache(  # type: ignore[no-untyped-def]
    path,
    rewrites: Iterable[tuple[str, str, str]],
    intents: Iterable[tuple[str, str]],
    header: str,
    summaries: Mapping[str, Mapping[str, Any]] | None = None,
) -> None:
    """Write the cache file: rewrites (message, key, payload), then intents (question,
    payload), then the turn summaries (question, inline JSON object of the summary), in
    the deterministic order given."""
    lines = [header, "rewrites:\n"]
    for message, key, payload in rewrites:
        lines.append(f"- message: {json.dumps(message)}\n")
        lines.append(f"  key: {json.dumps(key)}\n")
        lines.append(f"  payload: {json.dumps(payload)}\n")
    lines.append("intents:\n")
    for question, payload in intents:
        lines.append(f"- question: {json.dumps(question)}\n")
        lines.append(f"  payload: {json.dumps(payload)}\n")
    if summaries is not None:
        lines.append("summaries:\n")
        for question, summary in summaries.items():
            lines.append(f"- question: {json.dumps(question)}\n")
            lines.append(f"  payload: {json.dumps(summary, ensure_ascii=False)}\n")
    path.write_text("".join(lines), encoding="utf-8")
