"""The ask pipeline as a LangGraph graph: one node per stage of the combined-answer use
case, wired in the shape of ``docs/graphs/ask_graph.md``.

``build_ask_graph`` takes the use-case dependencies and closes over them — no module
globals, and no business rule in this module: every node is a thin wrapper around one
stage function from ``ndis.application.ask``. ``parse_intent`` routes either to
``refuse`` (a pre-execution refusal; nothing else runs) or to ``start_sections``, whose
three outgoing edges run the provider, price and rule sections in parallel in one
super-step. All three run every time — a section whose part of the intent is null
returns an empty update — so ``evidence`` is joined once after all of them, then
``guard`` checks the composed document and the run ends. The final answer is composed
from the graph's final state with :func:`ndis.application.ask.compose_stage`.

Two optional inputs exist for the chat graph's resumed turns (ticket 18) and change
nothing when absent: ``parsed`` skips the model's intent parse, and ``chosen_locality``
pins the place the user picked while the turn was paused. ``POST /ask`` and the parity
eval always invoke with ``{"question": ...}`` alone.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ndis.application.ask import (
    GUARD_FAILURE_EXPLANATION,
    REFUSAL_GUARD_FAILED,
    AskDeps,
    AskState,
    evidence_stage,
    guard_stage,
    parse_intent_stage,
    pre_execution_refusal,
    price_section_stage,
    provider_section_stage,
    rule_section_stage,
)


def build_ask_graph(
    deps: AskDeps, checkpointer: BaseCheckpointSaver | bool | None = None
) -> CompiledStateGraph:
    """The compiled ask graph over the given use-case dependencies.

    ``checkpointer`` is passed straight to ``compile()``: ``None`` (the default) keeps
    the ticket 15 behaviour — no checkpointer of its own, but inheritable from a parent
    graph when embedded as a subgraph — while ``False`` refuses any checkpointer, even
    one a parent lends through the run config. The chat graph embeds this graph inside a
    node function and compiles it with ``False``: its state carries page text, which must
    never be checkpointed, so the inner invoke stays ephemeral."""

    def parse_intent_node(state: AskState) -> dict[str, Any]:
        if state.get("parsed") is not None:
            # The chat graph hands the parse in (ticket 18: a resumed turn must not
            # parse twice); the pipeline then starts from it directly.
            return {}
        outcome = parse_intent_stage(deps, state["question"])
        return {
            "question": outcome.question,
            "parsed": outcome.parsed,
            "steps": [outcome.step],
        }

    def refuse_node(state: AskState) -> dict[str, Any]:
        refusal = pre_execution_refusal(state["parsed"])
        message = "the conditional edge routes here only when a refusal was decided"
        assert refusal is not None, message
        reason, explanation = refusal
        return {"refused": True, "reason": reason, "explanation": explanation}

    def start_sections_node(state: AskState) -> dict[str, Any]:
        del state
        return {}

    def providers_node(state: AskState) -> dict[str, Any]:
        intent = state["parsed"].intent
        if intent is None or intent.provider_filters is None:
            return {}
        outcome = provider_section_stage(
            deps,
            state["question"],
            intent.provider_filters,
            chosen_locality=state.get("chosen_locality"),
        )
        return {
            "providers": outcome.providers,
            "provider_total": outcome.total,
            "distances": outcome.distances,
            "distance_origin": outcome.origin,
            "locality": outcome.locality,
            "guard_rejections": outcome.rejections,
            "steps": [outcome.step],
        }

    def price_node(state: AskState) -> dict[str, Any]:
        intent = state["parsed"].intent
        if intent is None or intent.price_query is None:
            return {}
        outcome = price_section_stage(deps, intent.price_query)
        return {
            "price": outcome.price,
            "price_error": outcome.price_error,
            "service_prices": outcome.service_prices,
            "steps": [outcome.step],
        }

    def rule_node(state: AskState) -> dict[str, Any]:
        intent = state["parsed"].intent
        if intent is None or not intent.rule_question:
            return {}
        outcome = rule_section_stage(deps, intent.rule_question)
        return {"rule": outcome.rule, "steps": [outcome.step]}

    def evidence_node(state: AskState) -> dict[str, Any]:
        outcome = evidence_stage(deps, state.get("providers", ()))
        return {
            "services": outcome.services,
            "page_texts": outcome.page_texts,
            "steps": [outcome.step],
        }

    def guard_node(state: AskState) -> dict[str, Any]:
        outcome = guard_stage(deps, state)
        update: dict[str, Any] = {
            "guard_valid": outcome.valid,
            "guard_violations": outcome.violations,
            "caveat": outcome.caveat,
            "steps": [outcome.step],
        }
        if not outcome.valid:
            update["refused"] = True
            update["reason"] = REFUSAL_GUARD_FAILED
            update["explanation"] = GUARD_FAILURE_EXPLANATION
        return update

    def route_after_parse(state: AskState) -> str:
        return "refusal" if pre_execution_refusal(state["parsed"]) else "ok"

    builder = StateGraph(AskState)
    builder.add_node("parse_intent", parse_intent_node)
    builder.add_node("refuse", refuse_node)
    builder.add_node("start_sections", start_sections_node)
    builder.add_node("providers", providers_node)
    builder.add_node("price", price_node)
    builder.add_node("rule", rule_node)
    builder.add_node("evidence", evidence_node)
    builder.add_node("guard", guard_node)
    builder.add_edge(START, "parse_intent")
    builder.add_conditional_edges(
        "parse_intent", route_after_parse, {"refusal": "refuse", "ok": "start_sections"}
    )
    builder.add_edge("start_sections", "providers")
    builder.add_edge("start_sections", "price")
    builder.add_edge("start_sections", "rule")
    builder.add_edge("providers", "evidence")
    builder.add_edge("price", "evidence")
    builder.add_edge("rule", "evidence")
    builder.add_edge("evidence", "guard")
    builder.add_edge("guard", END)
    builder.add_edge("refuse", END)
    return builder.compile(checkpointer=checkpointer)
