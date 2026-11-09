"""Generate the judge-faithfulness hand-label template: ``evals/judge_verdicts.yaml``.

Runs the real /ask-rule pipeline (the ticket 06 two-gate refusal: retrieval similarity
then judge) over twenty in-scope pricing-rule questions — the ten golden questions plus
the first ten extras that produce an answer — through the recording client so the same
run feeds the cost and latency report. Each answered question becomes one template row:
the question, the served answer, its verbatim-checked citations and the model judge's
own supported verdict. The ten golden rows are pre-labelled ``labelled_by: agent`` for
the builder to fill from the golden evidence; the other ten stay blank for the owner.

Refused questions are reported and skipped (a refusal has no free text to judge); the
similarity gate costs no chat call, so rejected candidates are cheap. Paced and cached:
golden questions embed from cache, extras embed once each.

Usage: ``set -a; . ~/.config/ndis/.env; set +a; LLM_MODEL=... uv run python
scripts/build_judge_verdicts.py [--extras N] [--interval SECONDS]``
"""

import argparse
import json
import sys
from pathlib import Path

from ndis.adapters.cached_embedding import CachedEmbeddingClient
from ndis.adapters.embedding_cache import EmbeddingCache
from ndis.adapters.golden_questions import read_golden
from ndis.adapters.llm_client import OpenAICompatibleClient
from ndis.adapters.rule_vector_store import SqliteVecRuleStore
from ndis.adapters.tracing import flush, traced
from ndis.application.pricing import AskRule
from ndis.config import (
    embed_model,
    llm_api_key,
    llm_base_url,
    llm_configured,
    llm_model,
    rule_chunk_strategy,
    rule_similarity_threshold,
    rule_top_k,
)
from ndis.domain.extraction import TokenBudget
from scripts.recording import RecordingClient, run_log

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden.yaml"
INDEX = ROOT / "data" / "rule_index.db"
CACHE = ROOT / "data" / "private" / "embedding_cache.db"
TEMPLATE = ROOT / "evals" / "judge_verdicts.yaml"

HEADER = """\
# Judge-faithfulness hand-label template (ticket 08): twenty free-text answers the
# /ask-rule judge gate served, with the verdicts a person gives by reading the answer
# against its cited quotes. `verdict` is faithful | unfaithful | unsure; `labelled_by`
# records WHO labelled (agent = the building agent's initial labels, null = awaiting the
# owner). Agreement against the model judge's own supported verdict is reported in
# reports/cost.md with the denominators. Questions and answers are pricing-rule content
# only; no private data. Regenerate with scripts/build_judge_verdicts.py.
verdicts:
"""

# In-scope candidate questions beyond the golden ten, drawn from the 2025-26 Pricing
# Arrangements topics the index covers; taken in order until enough answer. Refused
# candidates (below the similarity gate, or the judge declining) are skipped.
EXTRA_QUESTIONS = (
    "Can a provider charge a cancellation fee when a participant does not attend a group session?",
    "What can a provider claim for vehicle costs when transporting a participant?",
    "Are provider travel labour costs claimable in remote areas, and how are they loaded?",
    "Can a key worker deliver early childhood early intervention supports?",
    "How does the Pricing Arrangements treat supports delivered in a group setting?",
    "Can a provider claim the cost of an interpreter for a therapy session?",
    "What must a provider do before increasing the price of a support?",
    "Can assistive technology be claimed through a daily activity support item?",
    "What does the Pricing Arrangements say about quotes for higher-cost supports?",
    "Are there limits on claiming transport for community participation supports?",
    "Can a support coordinator claim for time spent preparing plan review reports?",
    "How are therapy assistant hours claimed and at what rate?",
    "What applies when an unregistered provider delivers supports to a plan-managed participant?",
    "Can a provider claim for materials used while delivering a support?",
    "What must a provider do when a participant receives a support while in hospital?",
    "Are there restrictions on claiming supports delivered in schools?",
    "Do providers need a service agreement with the participant before delivering supports?",
    "Can a provider claim cleaning and maintenance costs for an SDA dwelling?",
    "Can plan managers charge participants a fee on top of the set price limits?",
    "How do weekday and weekend price limits differ for the same support item?",
    "Can a provider claim for a participant's transport in a group setting?",
    "What does the Pricing Arrangements say about supports provided by a therapy assistant?",
    "What can a provider claim for staff travel time between participants on the same day?",
    "Can a provider claim for preparing a participant's home safety report?",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extras", type=int, default=10, help="how many non-golden answers")
    parser.add_argument("--interval", type=float, default=7.0)
    args = parser.parse_args()
    if not llm_configured() or not embed_model():
        raise SystemExit("LLM_* and EMBED_MODEL must be set (source ~/.config/ndis/.env)")
    if not INDEX.is_file():
        raise SystemExit(f"rule index missing: {INDEX}")

    log = run_log("judge-generation")
    store = SqliteVecRuleStore(INDEX)
    cache = EmbeddingCache(CACHE)
    real = OpenAICompatibleClient(
        base_url=llm_base_url(),
        api_key=llm_api_key(),
        model=llm_model(),
        embed_model=embed_model(),
        budget=TokenBudget(1_000_000),
        interval=args.interval,
        backoff_base=20.0,
        backoff_cap=75.0,
    )
    chat = RecordingClient(traced(real, "rule_judge"), "rule_judge", log)
    embedder = CachedEmbeddingClient(
        RecordingClient(traced(real, "rule_embedding"), "rule_embedding", log),
        cache,
        embed_model(),
    )
    answer = AskRule(
        embedder,
        store,
        chat,
        strategy=rule_chunk_strategy(),
        threshold=rule_similarity_threshold(),
        top_k=rule_top_k(),
    )
    golden = read_golden(GOLDEN)
    questions = [question.question for question in golden.questions]
    extras_budget = args.extras
    all_questions = questions + list(EXTRA_QUESTIONS)
    rows = existing_rows(TEMPLATE)
    if rows:
        print(f"resuming: {len(rows)} rows already in {TEMPLATE.name}")
    try:
        refused: list[tuple[str, str]] = []
        for question in all_questions:
            if len(rows) >= len(golden.questions) + extras_budget:
                break
            if any(row["question"] == question for row in rows):
                continue
            result = answer(question)
            if result.refused:
                refused.append((question, result.reason or "refused"))
                print(f"refused ({result.reason}): {question[:70]}")
                continue
            rows.append(
                {
                    "question": question,
                    "answer": result.answer,
                    "citations": json.dumps(
                        [{"page": page, "quote": quote} for page, quote in result.citations]
                    ),
                    "similarity": result.top_similarity,
                    "model_supported": True,
                    "verdict": None,
                    "labelled_by": "agent" if question in questions else None,
                }
            )
            print(f"answered {len(rows)}: {question[:70]}")
        if len(rows) < len(golden.questions) + extras_budget:
            print(f"WARNING: only {len(rows)} answered of {len(golden.questions) + extras_budget}")
        TEMPLATE.write_text(HEADER + render(rows), encoding="utf-8")
        print(f"wrote {TEMPLATE} with {len(rows)} rows; refused {len(refused)}; log {log}")
    finally:
        store.close()
        cache.close()
        chat.close()
        flush()
    return 0


FIELDS = (
    "question",
    "answer",
    "citations",
    "similarity",
    "model_supported",
    "verdict",
    "labelled_by",
)


def existing_rows(path: Path) -> list[dict[str, object]]:
    """The rows already in the template, so a rerun answers only the missing ones and
    never re-spends calls on questions already answered."""
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    in_section = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "verdicts:":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("- "):
            current = {}
            rows.append(current)
            line = "  " + line[2:]
        if current is None or ":" not in line:
            continue
        field, _, value = line.strip().partition(":")
        if field in FIELDS:
            current[field] = _scalar(value.strip())
    return rows


def _scalar(text: str) -> object:
    if text in ("null", "~", ""):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def render(rows: list[dict[str, object]]) -> str:
    """The committed template: flat JSON-quoted fields in the repo's YAML subset."""
    lines: list[str] = []
    for row in rows:
        lines.append("- question: " + json.dumps(row["question"]) + "\n")
        lines.append("  answer: " + json.dumps(row["answer"]) + "\n")
        lines.append("  citations: " + json.dumps(row["citations"]) + "\n")
        lines.append(f"  similarity: {row['similarity']:.4f}\n")
        lines.append(f"  model_supported: {str(row['model_supported']).lower()}\n")
        lines.append("  verdict: " + json.dumps(row["verdict"]) + "\n")
        lines.append("  labelled_by: " + json.dumps(row["labelled_by"]) + "\n")
    return "".join(lines)


if __name__ == "__main__":
    sys.exit(main())
