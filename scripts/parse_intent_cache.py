"""Capture the golden /ask questions' intent payloads from the configured LLM.

Writes ``evals/intent_cache.yaml`` — one raw model payload per question — so the eval
test can replay the intent parse without spending quota, and so a later regeneration
measures whether the model's intents drifted. Paced by the adapter (one request every
few seconds), because the free tier is rate-limited per minute and per day.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; uv run python scripts/parse_intent_cache.py``
"""

import argparse
import json
from pathlib import Path

from ndis.adapters.golden_questions import read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.tracing import flush, traced
from ndis.application.ask import INTENT_SYSTEM_PROMPT, intent_prompt
from ndis.application.extraction import LlmRequest
from ndis.config import llm_api_key, llm_base_url, llm_configured, llm_model
from ndis.domain.extraction import TokenBudget

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
CACHE = ROOT / "evals" / "intent_cache.yaml"
HEADER = """\
# Intent payloads for the /ask golden cases, captured from the configured LLM
# (the models come from the environment; see src/ndis/config.py). Regenerate with
# scripts/parse_intent_cache.py whenever the golden questions change; the eval replays
# these payloads through the real parser and the real deterministic execution.
intents:
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=6.0, help="seconds between requests")
    args = parser.parse_args()
    if not llm_configured():
        raise SystemExit("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be set")
    golden = read_golden(GOLDEN)
    questions = [case.question for case in golden.filter_cases]
    questions += [case.question for case in golden.refusal_cases]
    client = traced(
        OpenAICompatibleClient(
            base_url=llm_base_url(),
            api_key=llm_api_key(),
            model=llm_model(),
            budget=TokenBudget(1_000_000),
            interval=args.interval,
            backoff_base=20.0,
            backoff_cap=75.0,
        ),
        "intent_capture",
    )
    lines = [HEADER]
    try:
        for question in questions:
            result = client.complete(LlmRequest(INTENT_SYSTEM_PROMPT, intent_prompt(question)))
            lines.append(f"- question: {json.dumps(question)}")
            lines.append(f"  payload: {json.dumps(result.text)}")
            print(f"captured {len(lines) - 2}: {question[:60]}")
    finally:
        client.close()
        flush()
    CACHE.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    print(f"wrote {CACHE} ({len(questions)} payloads)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
