"""The OpenAI-compatible client against scripted httpx transports: usage accounting,
retry and backoff, permanent failures, budget gating and pacing."""

import json

import httpx
import pytest

from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.application.extraction import BudgetExhausted, LlmError, LlmRequest
from ndis.domain.extraction import TokenBudget

REQUEST = LlmRequest(system="system prompt", user="user prompt")
BODY = {
    "choices": [{"message": {"content": '{"claims": []}'}}],
    "usage": {"prompt_tokens": 120, "completion_tokens": 45},
}
BODY_TEXT = json.dumps(BODY)


class ScriptedTransport(httpx.BaseTransport):
    """Replays canned (status, headers, body) answers and records what was sent."""

    def __init__(self, answers: list[tuple[int, dict[str, str], str]]) -> None:
        self._answers = list(answers)
        self.requests: list[dict[str, object]] = []
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.requests.append(
            {"url": str(request.url), "authorization": request.headers.get("Authorization")}
        )
        status, headers, body = self._answers.pop(0)
        return httpx.Response(status, headers=headers, text=body, request=request)


class TimeoutTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)


def make(
    transcript: ScriptedTransport, budget: TokenBudget | None = None
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://llm.example/v1/",
        api_key="secret",
        model="model-x",
        budget=budget or TokenBudget(10**9),
        interval=6.0,
        clock=lambda: 0.0,
        sleep=lambda seconds: sleeps.append(seconds),
        transport=transcript,
    )


sleeps: list[float] = []


@pytest.fixture(autouse=True)
def clear_sleeps() -> None:
    sleeps.clear()


def test_success_parses_content_and_usage() -> None:
    transcript = ScriptedTransport([(200, {}, BODY_TEXT.replace("'", '"'))])
    result = make(transcript).complete(REQUEST)
    assert result.text == '{"claims": []}'
    assert (result.input_tokens, result.output_tokens) == (120, 45)
    assert transcript.requests[0]["authorization"] == "Bearer secret"
    assert transcript.requests[0]["url"] == "https://llm.example/v1/chat/completions"


def test_budget_is_debited_with_reported_usage() -> None:
    transcript = ScriptedTransport([(200, {}, BODY_TEXT.replace("'", '"'))])
    client = make(transcript, TokenBudget(1000))
    client.complete(REQUEST)
    assert client.budget.spent == 165
    assert client.budget.remaining() == 835


def test_429_is_retried_and_retry_after_is_honoured() -> None:
    transcript = ScriptedTransport(
        [(429, {"Retry-After": "9"}, "{}"), (200, {}, BODY_TEXT.replace("'", '"'))]
    )
    result = make(transcript).complete(REQUEST)
    assert result.text == '{"claims": []}'
    assert sleeps == [9.0]
    assert transcript.calls == 2


def test_500_backs_off_exponentially_then_gives_up() -> None:
    transcript = ScriptedTransport([(500, {}, "boom")] * 4)
    with pytest.raises(LlmError):
        make(transcript).complete(REQUEST)
    assert transcript.calls == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_client_error_is_permanent() -> None:
    transcript = ScriptedTransport([(401, {}, "no key")])
    with pytest.raises(LlmError):
        make(transcript).complete(REQUEST)
    assert transcript.calls == 1


def test_undecodable_body_is_retried_then_succeeds() -> None:
    transcript = ScriptedTransport(
        [(200, {}, "<html>gateway</html>"), (200, {}, BODY_TEXT.replace("'", '"'))]
    )
    result = make(transcript).complete(REQUEST)
    assert result.text == '{"claims": []}'
    assert transcript.calls == 2


def test_timeouts_are_retried_to_the_limit() -> None:
    with pytest.raises(LlmError):
        make(TimeoutTransport()).complete(REQUEST)
    assert sleeps == [2.0, 4.0, 8.0]


def test_empty_budget_refuses_the_request_before_sending() -> None:
    transcript = ScriptedTransport([])
    client = make(transcript, TokenBudget(5))
    with pytest.raises(BudgetExhausted):
        client.complete(REQUEST)
    assert transcript.calls == 0


def test_usage_falls_back_to_estimates_when_absent() -> None:
    body = '{"choices": [{"message": {"content": "12345678"}}]}'
    transcript = ScriptedTransport([(200, {}, body)])
    result = make(transcript).complete(REQUEST)
    prompt_chars = len(REQUEST.system) + len(REQUEST.user)
    assert result.input_tokens == prompt_chars // 4 + 1
    assert result.output_tokens == 8 // 4 + 1


def test_requests_are_paced_by_the_interval() -> None:
    answers = [(200, {}, BODY_TEXT.replace("'", '"'))] * 3
    transcript = ScriptedTransport(answers)
    client = make(transcript)
    for _ in range(3):
        client.complete(REQUEST)
    assert sleeps == [6.0, 6.0]
