# LangGraph map (tickets 15–16)

The plain-words glossary for the graph layer introduced in ticket 15, with the Android
comparison the ticket brief used and where each concept is visible in this repository.
The locked version is `langgraph` 1.2.x; the API reference lives at
<https://docs.langchain.com/oss/python/langgraph/graph-api>.

| Concept | Plain meaning | Android comparison | File where it is visible | Ticket |
| --- | --- | --- | --- | --- |
| State | One dictionary that travels through the graph; a node reads it and returns only the keys it wants to change | A `UiState` data class each step copies with a few fields changed | `AskState` in `src/ndis/application/ask.py`, updated by the nodes in `src/ndis/adapters/graphs/ask_graph.py`; `ChatState` in `src/ndis/application/chat.py` | 15 |
| Node | A plain Python function `state -> partial update`; one box in the flowchart | One function in a coroutine chain that takes the current state and returns the next | `parse_intent_node`, `providers_node`, ... inside `build_ask_graph` (`adapters/graphs/ask_graph.py`), each wrapping one stage from `application/ask.py`; `contextualise_node`, `ask_node`, `remember_turn_node` in `adapters/graphs/chat_graph.py` | 15 |
| Edge | The arrow: a normal edge always goes A -> B | A fixed `navigate()` call from one screen to the next | `builder.add_edge("evidence", "guard")` in `adapters/graphs/ask_graph.py` | 15 |
| Conditional edge | A function that looks at the state and picks the arrow (and its labels are what the diagram shows) | Navigation that picks the destination from the current state | `builder.add_conditional_edges("parse_intent", route_after_parse, {"refusal": "refuse", "ok": "start_sections"})`; `add_conditional_edges(START, route_after_start, {"first_turn": "ask", "follow_up": "contextualise"})` — START itself can be the source | 15 |
| Super-step | Nodes due at the same time run together as one step; `providers`, `price` and `rule` share one and run on separate worker threads | Launching three coroutines in one `launch` block and awaiting them together | The three `add_edge("start_sections", ...)` edges fanning out, and the join at `evidence`, in `adapters/graphs/ask_graph.py`; the concurrency test in `tests/test_ask_graph.py` proves the threads are real | 15 |
| Reducer | When two nodes in the same super-step write the same key, the reducer says how to merge (here: append to the list); without one, LangGraph refuses the double write | A `MutableStateFlow.update {}` reducer folding concurrent writes | `steps: Annotated[list[Step], operator.add]` on `AskState` in `application/ask.py` | 15 |
| Reducer on a list (memory) | The same append reducer is how a conversation accumulates: each turn appends one entry to `turns` and nothing ever rewrites the earlier ones | `MutableList.add()` growing a saved list across process restarts | `turns: Annotated[list[ChatTurn], operator.add]` on `ChatState` in `application/chat.py`, appended by `remember_turn_node` in `adapters/graphs/chat_graph.py` | 16 |
| Compile / invoke | `compile()` turns the description into something runnable and checks the structure; `invoke(input)` runs it to the final state | `build()` on a builder, then `execute()` | `return builder.compile()` in `adapters/graphs/ask_graph.py`; `graph.invoke({"question": ...})` in the tests, the parity eval and `scripts/run_ask_graph.py` | 15 |
| Checkpointer | An object you hand to `compile()`; after every super-step it saves the whole state, so the next invocation of the same thread resumes from it | `SavedStateHandle`, which keeps screen state across process death | `InMemorySaver` created once in `api/dependencies.chat_checkpointer()`; `build_chat_graph(deps, checkpointer=...)` in `adapters/graphs/chat_graph.py` | 16 |
| Thread | One conversation: every call passes `{"configurable": {"thread_id": ...}}`; the same id loads the saved state, a new id starts empty. The browser remembers only the id | One `SavedStateHandle` per screen identity | The config in `api/app.py`'s `chat` handler; `graph.get_state({"configurable": {"thread_id": ...}})` in the tests; thread ids are UUID4s the server issues | 16 |
| Subgraph | A compiled graph used inside another graph. When the parent has a checkpointer, a compiled graph added as a node **inherits** it and its channels are checkpointed too (verified in the installed 1.2.24 source: the parent lends its checkpointer through the run config, and `_resolve_checkpointer` uses it unless the subgraph compiled with `checkpointer=False`) | A nested `NavController` that would otherwise inherit the parent's saved-state registry | The ticket 15 ask graph is invoked **inside** `ask_node` in `adapters/graphs/chat_graph.py`, compiled with `checkpointer=False` so its page-text state stays ephemeral; `tests/test_chat_graph.py` proves no checkpoint carries page text | 16 |

Two behaviours this ticket verified against the docs and the installed package before
relying on them:

- **The join.** A node with several incoming edges waits for every source *of the same
  super-step*. Because the three section nodes are all fed from `start_sections`, they
  run in one super-step and `evidence` runs exactly once after all three. A section
  whose part of the intent is null still runs and returns an empty update, so the join
  never sees fewer than three messages.
- **Streaming updates.** `stream(..., stream_mode="updates", version="v2")` yields one
  `{"type": "updates", "ns": (), "data": {node: update}}` chunk per node as it finishes;
  a node that returned an empty update streams as `None`. `--diagram` on the runner
  script prints `get_graph().draw_mermaid()`, and a test fails when the committed copy
  in `docs/graphs/ask_graph.md` drifts from it.
