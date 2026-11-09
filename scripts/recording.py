"""The latency/token recorder the cost measurements run through.

``RecordingClient`` wraps a real ``OpenAICompatibleClient`` and appends one JSON line per
call — site, endpoint, model, reported token usage and wall-clock seconds — to a private
JSONL file under ``data/private/cost/``. The raw measurements stay uncommitted; the
report generator (``scripts/cost_report.py``) reads them and renders ``reports/cost.md``.
"""

import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ndis.application.extraction import LlmRequest, LlmResult
from ndis.application.pricing import EmbeddingsResult

ROOT = Path(__file__).resolve().parents[1]
COST_DIR = ROOT / "data" / "private" / "cost"


class RecordingClient:
    """Wraps one real client (completion and embeddings), timing every call and logging
    its usage; everything else delegates untouched."""

    def __init__(self, inner, site: str, log: Path) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner
        self._site = site
        self._log = log
        self._model = getattr(inner, "model", "")
        self._embed_model = getattr(inner, "embed_model", "") or ""

    def complete(self, request: LlmRequest) -> LlmResult:
        started = time.monotonic()
        result = self._inner.complete(request)
        self._record_result("chat/completions", self._model, result, time.monotonic() - started)
        return result

    def embed(self, texts: Sequence[str]) -> EmbeddingsResult:
        started = time.monotonic()
        result = self._inner.embed(texts)
        self._record_result("embeddings", self._embed_model, result, time.monotonic() - started)
        return result

    @property
    def budget(self):  # type: ignore[no-untyped-def]
        return self._inner.budget

    def close(self) -> None:
        self._inner.close()

    def _record_result(
        self, endpoint: str, model: str, result: LlmResult | EmbeddingsResult, seconds: float
    ) -> None:
        self._log.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "site": self._site,
            "endpoint": endpoint,
            "model": model,
            "input_tokens": result.input_tokens,
            "output_tokens": getattr(result, "output_tokens", 0),
            "seconds": round(seconds, 3),
        }
        with self._log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")


def run_log(name: str) -> Path:
    """One JSONL file per measured run, named so a rerun never mixes sessions."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return COST_DIR / f"{name}-{stamp}.jsonl"
