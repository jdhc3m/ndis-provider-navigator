"""The chat graph: conversation memory over the ticket 15 ask pipeline, wired in the
shape of ``docs/graphs/chat_graph.md``.

The first turn of a thread goes straight to ``parse_intent`` — no extra model call. A
follow-up turn is rewritten into a standalone question first (:func:`contextualise_stage`),
and the rewritten question is what the intent parse receives; the existing refusals then
run downstream of it exactly as they do for a first question, so a follow-up cannot
launder a decision request or an injection past them. ``remember_turn`` appends the turn
in its JSON-plain shape to ``turns``.

The pause (ticket 18) sits between the parse and the pipeline, in the graph's own
``precheck_locality`` node — the inner ask graph runs with ``checkpointer=False`` (ticket
16: its state carries page text, which must never be checkpointed), so an interrupt
inside it could not pause anything. The precheck runs only the pure planning of the
provider section (:func:`ndis.application.ask.plan_provider_section`): the postcode guard
and the deterministic gazetteer lookup, no model call and no write, because on resume
LangGraph re-runs the node from its first line and everything before ``interrupt()`` runs
twice. When the suburb is ambiguous with no stated postcode to carry the filter, the node
interrupts with the candidates; the resume answer is validated against the offered
options by :func:`ndis.application.chat.locality_choice_of`, and a value naming none of
them is recorded as ``locality_error`` and routed back to the same node by a conditional
edge — one ``interrupt()`` call per node run, never a loop. A valid choice is written to
``chosen_locality`` as plain strings and the ask node pins the pipeline's provider query
with it, so the answer is the one that locality would have had all along.

The ticket 15 ask graph becomes the ``ask`` node: it is invoked **inside** a node
function, compiled with ``checkpointer=False`` so the parent's checkpointer cannot reach
it, and receives the already-parsed intent and the parse step in its input, so a resumed
turn never parses twice. The inner invoke is ephemeral; only the outer chat state, which
holds JSON-plain values only, is ever saved.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from ndis.adapters.graphs.ask_graph import build_ask_graph
from ndis.application.ask import (
    compose_stage,
    parse_intent_stage,
    plan_provider_section,
    step_document,
    step_of,
)
from ndis.application.chat import (
    INVALID_LOCALITY_OPTION,
    ChatDeps,
    ChatState,
    contextualise_stage,
    locality_choice_of,
    locality_pending_question,
    standalone_question_of,
    turn_document,
)
from ndis.domain.intent import parsed_intent_document, parsed_intent_of
from ndis.domain.locality import locality_document, locality_of


def build_chat_graph(
    deps: ChatDeps, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """The compiled chat graph over the given use-case dependencies, saving its state
    with the given checkpointer (one per process, shared by every request and thread)."""
    ask_graph = build_ask_graph(deps.ask_deps, checkpointer=False)

    def route_after_start(state: ChatState) -> str:
        """First turn when the thread holds no completed turn yet, follow-up otherwise."""
        return "follow_up" if state.get("turns") else "first_turn"

    def contextualise_node(state: ChatState) -> dict[str, Any]:
        outcome = contextualise_stage(deps, state.get("turns", ()), state["message"])
        return {
            "standalone_question": outcome.standalone_question,
            "rewritten": outcome.rewritten,
            "rewrite_reason": outcome.reason,
        }

    def parse_intent_node(state: ChatState) -> dict[str, Any]:
        """Parse the standalone question into an intent — the turn's one model call —
        and reset the turn-scoped pause keys, so a previous turn's leftover choice or
        error can never leak into this one."""
        outcome = parse_intent_stage(deps.ask_deps, standalone_question_of(state))
        return {
            "parsed_intent": parsed_intent_document(outcome.parsed),
            "parse_step": step_document(outcome.step),
            "chosen_locality": None,
            "locality_error": None,
        }

    def precheck_locality_node(state: ChatState) -> dict[str, Any]:
        """Resolve the locality purely; when the question cannot run without knowing
        which place the user meant, pause for their choice.

        Everything above the ``interrupt()`` is the pure planning the provider section
        performs before executing anything — no model call, no write — because on resume
        this node starts again from its first line. The single ``interrupt()`` call site
        pauses the run with the pending question (carrying the recorded reason when the
        last answer was refused); a resume value that names none of the offered options
        is recorded in ``locality_error`` and the conditional edge routes back here,
        where the re-ask pauses with that reason in the payload."""
        parsed = parsed_intent_of(state["parsed_intent"])
        intent = parsed.intent
        if intent is None or intent.provider_filters is None:
            return {"chosen_locality": None, "locality_error": None}
        filters = intent.provider_filters
        if filters.locality is None:
            return {"chosen_locality": None, "locality_error": None}
        plan = plan_provider_section(deps.ask_deps, standalone_question_of(state), filters)
        if not plan.needs_locality_choice:
            return {"chosen_locality": None, "locality_error": None}
        payload = locality_pending_question(plan.resolution)  # type: ignore[arg-type]
        error = state.get("locality_error")
        if error is not None:
            payload = {**payload, "reason": error}
        choice = interrupt(payload)
        chosen = locality_choice_of(choice, plan.resolution)  # type: ignore[arg-type]
        if chosen is None:
            return {"locality_error": INVALID_LOCALITY_OPTION}
        return {"chosen_locality": locality_document(chosen), "locality_error": None}

    def route_after_precheck(state: ChatState) -> str:
        """Back to the same question when the last answer named none of the options."""
        return "reask" if state.get("locality_error") else "answer"

    def ask_node(state: ChatState) -> dict[str, Any]:
        """Run the ticket 15 ask pipeline on the standalone question, handing it the
        already-parsed intent and the parse step so a resumed turn never parses twice,
        plus the chosen locality when the paused turn collected one. The inner invoke is
        ephemeral (the ask graph refuses any checkpointer) and only its composed answer,
        already converted to the API's JSON shape, enters the chat state."""
        ask_input: dict[str, Any] = {
            "question": standalone_question_of(state),
            "parsed": parsed_intent_of(state["parsed_intent"]),
            "steps": [step_of(state["parse_step"])],
        }
        if state.get("chosen_locality"):
            ask_input["chosen_locality"] = locality_of(state["chosen_locality"])
        answer = compose_stage(ask_graph.invoke(ask_input))
        return {"answer": deps.answer_to_json(answer)}

    def remember_turn_node(state: ChatState) -> dict[str, Any]:
        turn = turn_document(
            state["message"],
            standalone_question_of(state),
            state.get("rewritten", False),
            state.get("rewrite_reason"),
            state["answer"],
        )
        return {"turns": [turn]}

    builder = StateGraph(ChatState)
    builder.add_node("contextualise", contextualise_node)
    builder.add_node("parse_intent", parse_intent_node)
    builder.add_node("precheck_locality", precheck_locality_node)
    builder.add_node("ask", ask_node)
    builder.add_node("remember_turn", remember_turn_node)
    builder.add_conditional_edges(
        START, route_after_start, {"first_turn": "parse_intent", "follow_up": "contextualise"}
    )
    builder.add_edge("contextualise", "parse_intent")
    builder.add_edge("parse_intent", "precheck_locality")
    builder.add_conditional_edges(
        "precheck_locality", route_after_precheck, {"answer": "ask", "reask": "precheck_locality"}
    )
    builder.add_edge("ask", "remember_turn")
    builder.add_edge("remember_turn", END)
    return builder.compile(checkpointer=checkpointer)
