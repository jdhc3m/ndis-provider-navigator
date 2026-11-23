"""The rewrite guard in domain/chat: the model's standalone-question output is untrusted,
so anything but a non-empty string of at most 500 characters falls back to the user's
raw message with a machine reason."""

import pytest

from ndis.domain.chat import (
    MAX_STANDALONE_CHARS,
    REWRITE_EMPTY,
    REWRITE_FIELD_MISSING,
    REWRITE_NOT_JSON,
    REWRITE_NOT_OBJECT,
    REWRITE_TOO_LONG,
    REWRITE_WRONG_TYPE,
    parse_standalone_question,
)

MESSAGE = "only the approved ones"


def test_a_valid_rewrite_is_accepted_and_stripped() -> None:
    outcome = parse_standalone_question(
        '{"standalone_question": "  approved plan managers in Paddington QLD  "}', MESSAGE
    )
    assert outcome.question == "approved plan managers in Paddington QLD"
    assert outcome.rewritten is True
    assert outcome.reason is None


def test_a_500_character_rewrite_is_accepted() -> None:
    question = "x" * MAX_STANDALONE_CHARS
    outcome = parse_standalone_question(f'{{"standalone_question": "{question}"}}', MESSAGE)
    assert outcome.rewritten is True and outcome.question == question


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ("not json at all", REWRITE_NOT_JSON),
        ("[1, 2, 3]", REWRITE_NOT_OBJECT),
        ('{"question": "approved plan managers in Paddington QLD"}', REWRITE_FIELD_MISSING),
        ('{"standalone_question": 42}', REWRITE_WRONG_TYPE),
        ('{"standalone_question": ["a", "b"]}', REWRITE_WRONG_TYPE),
        ('{"standalone_question": null}', REWRITE_WRONG_TYPE),
        ('{"standalone_question": ""}', REWRITE_EMPTY),
        ('{"standalone_question": "    "}', REWRITE_EMPTY),
        ('{"standalone_question": "' + "x" * (MAX_STANDALONE_CHARS + 1) + '"}', REWRITE_TOO_LONG),
    ],
)
def test_every_bad_rewrite_falls_back_to_the_raw_message(payload: str, reason: str) -> None:
    outcome = parse_standalone_question(payload, MESSAGE)
    assert outcome.question == MESSAGE
    assert outcome.rewritten is False
    assert outcome.reason == reason
