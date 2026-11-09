"""An ``LlmClient`` over any OpenAI-compatible chat endpoint, speaking the
``POST {base_url}/chat/completions`` wire format with JSON-object responses.

Run hygiene, all enforced here for every call:

- Requests are at least ``interval`` seconds apart (measured on a monotonic clock from the
  start of the previous request), because free-tier endpoints are rate-limited per minute.
- A transient answer (429, 5xx, timeout, dropped connection, or a 2xx whose body is not
  JSON) is retried up to ``max_attempts`` with exponential backoff (``backoff_base``
  doubling, capped at ``backoff_cap``); a ``Retry-After`` header is honoured when longer.
  A 4xx answer is permanent: ``LlmError`` with no retry.
- The per-run ``TokenBudget`` is checked before a request is sent (against the domain's
  deterministic character estimate) and debited with the endpoint's reported usage after
  it; ``BudgetExhausted`` is raised when the next request cannot fit.
- The API key travels only in the ``Authorization`` header and is never logged.
"""

import json
import time
from collections.abc import Callable

import httpx

from ndis.application.extraction import BudgetExhausted, LlmError, LlmRequest, LlmResult
from ndis.domain.extraction import TokenBudget, estimated_tokens

DEFAULT_INTERVAL_SECONDS = 6.0
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 30.0


class OpenAICompatibleClient:
    """Paced, budgeted ``LlmClient`` for one run; ``clock`` and ``sleep`` are injectable."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        budget: TokenBudget,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_base: float = BACKOFF_BASE_SECONDS,
        backoff_cap: float = BACKOFF_CAP_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._model = model
        self._budget = budget
        self._interval = interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    @property
    def budget(self) -> TokenBudget:
        return self._budget

    def complete(self, request: LlmRequest) -> LlmResult:
        prompt_chars = len(request.system) + len(request.user)
        if self._budget.would_exceed(estimated_tokens("x" * prompt_chars)):
            raise BudgetExhausted(
                f"budget {self._budget.spent}/{self._budget.limit} cannot fit the request"
            )
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_output_tokens,
        }
        for attempt in range(1, self._max_attempts + 1):
            if attempt == 1:
                self._pace()
            response = self._post(body, attempt)
            if response is None:
                continue
            outcome = _parse_success(response, prompt_chars)
            if outcome is None:
                continue  # undecodable 2xx body: one more attempt may spell it correctly
            text, input_tokens, output_tokens = outcome
            self._budget = self._budget.spend(input_tokens + output_tokens)
            return LlmResult(text, input_tokens, output_tokens)
        raise LlmError(f"endpoint failed after {self._max_attempts} attempts")

    def _backoff(self, attempt: int) -> float:
        return min(self._backoff_cap, self._backoff_base * 2 ** (attempt - 1))

    def _pace(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            delay = self._last_request_at + self._interval - now
            if delay > 0:
                self._sleep(delay)
        self._last_request_at = self._clock()

    def _post(self, body: dict[str, object], attempt: int) -> httpx.Response | None:
        """One POST; ``None`` means the failure was transient and worth another attempt.

        The backoff itself is the wait before the next attempt, so the pacing clock is
        advanced across it and the last attempt of a request sleeps no more."""
        try:
            response = self._client.post(self._base_url + "chat/completions", json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            self._wait_before_retry(attempt)
            return None
        if response.status_code == 429 or response.status_code >= 500:
            header = response.headers.get("Retry-After", "")
            try:
                retry_after = max(0.0, float(header))
            except ValueError:
                retry_after = 0.0
            self._wait_before_retry(attempt, at_least=retry_after)
            return None
        if response.status_code >= 400:
            raise LlmError(f"endpoint rejected the request: HTTP {response.status_code}")
        return response

    def _wait_before_retry(self, attempt: int, at_least: float = 0.0) -> None:
        if attempt >= self._max_attempts:
            return
        delay = min(self._backoff_cap, max(self._backoff(attempt), at_least))
        self._sleep(delay)
        self._last_request_at = self._clock()


def _parse_success(response: httpx.Response, prompt_chars: int) -> tuple[str, int, int] | None:
    """``(text, input_tokens, output_tokens)`` from a 2xx body, or ``None`` when the body
    is not the JSON shape we asked for (worth one more attempt)."""
    try:
        document = json.loads(response.text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    choices = document.get("choices")
    usage = document.get("usage")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    input_tokens = estimated_tokens("x" * prompt_chars)
    output_tokens = estimated_tokens(content)
    if isinstance(usage, dict):
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int):
            input_tokens, output_tokens = prompt, completion
    return content, input_tokens, output_tokens
