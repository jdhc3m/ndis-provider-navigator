"""Run one question through the ask graph (ticket 15): print each node's state update as
it finishes and the composed answer, or print the graph's Mermaid diagram with
``--diagram``.

The live wiring mirrors the API's composition root (``ndis.api.dependencies``):
repositories over the database from ``NDIS_DB_PATH`` (default ``data/ndis.db``), the rule
answerer over the built index when present, evidence over the extraction cache and
stored pages. A real run makes exactly one model call — the intent parse — so the LLM
environment must be configured as for the API. ``--diagram`` builds the graph without
running any node and needs no data or key.
"""

import argparse
from collections.abc import Callable
from typing import Any

from ndis.adapters.graphs.ask_graph import build_ask_graph
from ndis.api.dependencies import (
    claim_evidence_source,
    locality_resolver,
    postcode_centroids,
    provider_repository,
    question_llm_client,
    rule_answerer_for,
    rule_store_maybe,
    support_item_repository,
)
from ndis.application.ask import AskDeps, MissingIndexRuleAnswerer, compose_stage
from ndis.application.catalogue import GetPriceLimit, SearchSupportItems
from ndis.application.providers import ListProviders
from ndis.config import llm_model, token_price


class Unused:
    """Placeholder dependency for ``--diagram``: building the graph runs no node, so a
    call would be a bug."""

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("the diagram build runs no node")

    def __getattr__(self, name: str) -> Callable[..., object]:
        raise RuntimeError("the diagram build runs no node")


def diagram_deps() -> AskDeps:
    """Dependencies that are never called, for building the diagram only."""
    return AskDeps(
        client=Unused(),
        providers=Unused(),
        catalogue=Unused(),
        rule_answerer=MissingIndexRuleAnswerer(),
        evidence=Unused(),
        locality_resolver=Unused(),
        service_search=Unused(),
    )


def build_deps() -> AskDeps:
    """The same wiring the API uses for ``POST /ask``, without FastAPI."""
    return AskDeps(
        client=question_llm_client(),
        providers=ListProviders(provider_repository(), postcode_centroids()),
        catalogue=GetPriceLimit(support_item_repository()),
        rule_answerer=rule_answerer_for(rule_store_maybe()),
        evidence=claim_evidence_source(),
        locality_resolver=locality_resolver(),
        service_search=SearchSupportItems(support_item_repository()),
        token_prices=token_price(llm_model()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one question through the ask graph.")
    parser.add_argument("question", nargs="*", help="the question to answer")
    parser.add_argument("--diagram", action="store_true", help="print the Mermaid diagram and exit")
    args = parser.parse_args()
    if args.diagram:
        print(build_ask_graph(diagram_deps()).get_graph().draw_mermaid())
        return 0
    question = " ".join(args.question).strip()
    if not question:
        parser.error("a question is required unless --diagram is given")
    graph = build_ask_graph(build_deps())
    final: dict[str, Any] = {}
    for chunk in graph.stream({"question": question}, stream_mode="updates", version="v2"):
        for node, update in chunk["data"].items():
            print(f"{node}: {update!r}")
            if isinstance(update, dict):
                steps = final.setdefault("steps", [])
                steps.extend(update.get("steps", []))
                final.update({key: value for key, value in update.items() if key != "steps"})
    print(f"answer: {compose_stage(final)}")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
