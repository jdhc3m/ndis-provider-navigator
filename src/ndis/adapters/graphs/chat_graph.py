"""The chat graph: conversation memory over the ticket 15 ask pipeline, wired in the
shape of ``docs/graphs/chat_graph.md``.

The first turn of a thread goes straight to ``ask`` — no extra model call. A follow-up
turn is rewritten into a standalone question first (:func:`contextualise_stage`), and
the rewritten question is what ``ask`` receives; the existing refusals then run
downstream of it exactly as they do for a first question, so a follow-up cannot launder
a decision request or an injection past them. ``remember_turn`` appends the turn in its
JSON-plain shape to ``turns``.

The ticket 15 ask graph becomes the ``ask`` node: it is invoked **inside** a node
function, compiled with ``checkpointer=False`` so the parent's checkpointer cannot reach
it. This is deliberate: a compiled graph embedded as a true subgraph has its channels
checkpointed under the parent's checkpointer, and the ask state carries page text —
which must never be checkpointed. The inner invoke is ephemeral; only the outer chat
state, which holds JSON-plain values only, is ever saved.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ndis.adapters.graphs.ask_graph import build_ask_graph
from ndis.application.ask import compose_stage
from ndis.application.chat import (
    ChatDeps,
    ChatState,
    contextualise_stage,
    standalone_question_of,
    turn_document,
)


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

    def ask_node(state: ChatState) -> dict[str, Any]:
        """Run the ticket 15 ask graph on the standalone question; the inner invoke is
        ephemeral (the ask graph refuses any checkpointer) and only its composed answer,
        already converted to the API's JSON shape, enters the chat state."""
        answer = compose_stage(ask_graph.invoke({"question": standalone_question_of(state)}))
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
    builder.add_node("ask", ask_node)
    builder.add_node("remember_turn", remember_turn_node)
    builder.add_conditional_edges(
        START, route_after_start, {"first_turn": "ask", "follow_up": "contextualise"}
    )
    builder.add_edge("contextualise", "ask")
    builder.add_edge("ask", "remember_turn")
    builder.add_edge("remember_turn", END)
    return builder.compile(checkpointer=checkpointer)
