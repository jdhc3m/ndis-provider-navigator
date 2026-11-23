"""Capture the golden /ask questions' intent payloads from the configured LLM.

Writes ``evals/intent_cache.yaml`` — one raw model payload per question — so the eval
test can replay the intent parse without spending quota, and so a later regeneration
measures whether the model's intents drifted. Paced by the adapter (one request every
few seconds), because the free tier is rate-limited per minute and per day.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; uv run python scripts/parse_intent_cache.py``

``--only SUBSTRING`` (repeatable) captures only the golden questions containing the
substring — for a prompt tweak or reworded cases — and keeps every other question's
existing payload from the committed cache, so a small change does not re-spend the
whole capture. Run without ``--only`` to capture all questions from scratch.
"""

import argparse
import json
from pathlib import Path

from ndis.adapters.golden_questions import read_golden, read_intent_cache
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
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="capture only golden questions containing this substring (repeatable); "
        "every other question keeps its payload from the committed cache",
    )
    args = parser.parse_args()
    if not llm_configured():
        raise SystemExit("LLM_BASE_URL, LLM_MODEL and LLM_API_KEY must be set")
    golden = read_golden(GOLDEN)
    questions = [case.question for case in golden.filter_cases]
    questions += [case.question for case in golden.locality_cases]
    questions += [case.question for case in golden.service_cases]
    questions += [case.question for case in golden.refusal_cases]
    existing: dict[str, str] = {}
    if args.only and CACHE.is_file():
        existing = read_intent_cache(CACHE)
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
    captured: dict[str, str] = {}
    fresh = 0
    kept = 0
    try:
        for question in questions:
            if args.only and not any(needle in question for needle in args.only):
                prior = existing.get(question)
                if prior is None:
                    raise SystemExit(
                        f"no committed payload for {question!r}; "
                        "run without --only once to capture every question"
                    )
                captured[question] = prior
                kept += 1
                continue
            result = client.complete(LlmRequest(INTENT_SYSTEM_PROMPT, intent_prompt(question)))
            captured[question] = result.text
            fresh += 1
            print(f"captured: {question[:60]}")
    finally:
        client.close()
        flush()
    lines = [HEADER]
    for question in questions:
        lines.append(f"- question: {json.dumps(question)}")
        lines.append(f"  payload: {json.dumps(captured[question])}")
    CACHE.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    print(f"wrote {CACHE} ({fresh} captured, {kept} kept, {len(questions)} payloads)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
