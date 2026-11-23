"""Pure rules for turning one follow-up message into a standalone question, and the
deterministic guard over the model's rewrite.

The rewrite is a model call like every other, so its output is untrusted input: the
parser here is total (it never raises on model output) and accepts only a non-empty
string of at most :data:`MAX_STANDALONE_CHARS` characters. Anything else falls back to
the user's raw message with a machine-readable reason, so a bad rewrite can only ever
degrade to "the question is parsed as the user wrote it" — never a crash and never a
half-understood execution.
"""

import json
from dataclasses import dataclass

# The same cap the HTTP layer puts on a question: a longer "question" is not something
# the intent parser should see, whatever the model rewrote.
MAX_STANDALONE_CHARS = 500

# Machine reasons for falling back to the user's raw message; the turn stores one so a
# degraded rewrite is disclosed, never silent.
REWRITE_NOT_JSON = "rewrite_not_json"
REWRITE_NOT_OBJECT = "rewrite_not_object"
REWRITE_FIELD_MISSING = "rewrite_field_missing"
REWRITE_WRONG_TYPE = "rewrite_wrong_type"
REWRITE_EMPTY = "rewrite_empty"
REWRITE_TOO_LONG = "rewrite_too_long"

STANDALONE_FIELD = "standalone_question"


@dataclass(frozen=True)
class StandaloneQuestion:
    """What the follow-up is answered as: the standalone question when the model's
    rewrite passed the guard, or the user's raw message with the machine reason when it
    did not. ``rewritten`` is False exactly when a fallback happened."""

    question: str
    rewritten: bool
    reason: str | None = None


def parse_standalone_question(payload: str, raw_message: str) -> StandaloneQuestion:
    """Parse the model's rewrite JSON. Total: any structural surprise falls back to
    ``raw_message`` with a machine reason, never an exception.

    The rewrite must be a JSON object carrying ``standalone_question`` as a non-empty
    string of at most :data:`MAX_STANDALONE_CHARS` characters; the value is stripped of
    surrounding whitespace before the length and emptiness checks."""
    try:
        document = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return StandaloneQuestion(raw_message, False, REWRITE_NOT_JSON)
    if not isinstance(document, dict):
        return StandaloneQuestion(raw_message, False, REWRITE_NOT_OBJECT)
    if STANDALONE_FIELD not in document:
        return StandaloneQuestion(raw_message, False, REWRITE_FIELD_MISSING)
    value = document[STANDALONE_FIELD]
    if not isinstance(value, str):
        return StandaloneQuestion(raw_message, False, REWRITE_WRONG_TYPE)
    text = value.strip()
    if not text:
        return StandaloneQuestion(raw_message, False, REWRITE_EMPTY)
    if len(text) > MAX_STANDALONE_CHARS:
        return StandaloneQuestion(raw_message, False, REWRITE_TOO_LONG)
    return StandaloneQuestion(text, True)
