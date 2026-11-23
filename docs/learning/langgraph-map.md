# LangGraph map (ticket 15)

The plain-words glossary for the graph layer introduced in ticket 15, with the Android
comparison the ticket brief used and where each concept is visible in this repository.
The locked version is `langgraph` 1.2.x; the API reference lives at
<https://docs.langchain.com/oss/python/langgraph/graph-api>.

| Concept | Plain meaning | Android comparison | File where it is visible | Ticket |
| --- | --- | --- | --- | --- |
| State | One dictionary that travels through the graph; a node reads it and returns only the keys it wants to change | A `UiState` data class each step copies with a few fields changed | `AskState` in `src/ndis/application/ask.py`, updated by the nodes in `src/ndis/adapters/graphs/ask_graph.py` | 15 |
| Node | A plain Python function `state -> partial update`; one box in the flowchart | One function in a coroutine chain that takes the current state and returns the next | `parse_intent_node`, `providers_node`, ... inside `build_ask_graph` (`adapters/graphs/ask_graph.py`), each wrapping one stage from `application/ask.py` | 15 |
| Edge | The arrow: a normal edge always goes A -> B | A fixed `navigate()` call from one screen to the next | `builder.add_edge("evidence", "guard")` in `adapters/graphs/ask_graph.py` | 15 |
| Conditional edge | A function that looks at the state and picks the arrow (and its labels are what the diagram shows) | Navigation that picks the destination from the current state | `builder.add_conditional_edges("parse_intent", route_after_parse, {"refusal": "refuse", "ok": "start_sections"})` | 15 |
| Super-step | Nodes due at the same time run together as one step; `providers`, `price` and `rule` share one and run on separate worker threads | Launching three coroutines in one `launch` block and awaiting them together | The three `add_edge("start_sections", ...)` edges fanning out, and the join at `evidence`, in `adapters/graphs/ask_graph.py`; the concurrency test in `tests/test_ask_graph.py` proves the threads are real | 15 |
| Reducer | When two nodes in the same super-step write the same key, the reducer says how to merge (here: append to the list); without one, LangGraph refuses the double write | A `MutableStateFlow.update {}` reducer folding concurrent writes | `steps: Annotated[list[Step], operator.add]` on `AskState` in `application/ask.py` | 15 |
| Compile / invoke | `compile()` turns the description into something runnable and checks the structure; `invoke(input)` runs it to the final state | `build()` on a builder, then `execute()` | `return builder.compile()` in `adapters/graphs/ask_graph.py`; `graph.invoke({"question": ...})` in the tests, the parity eval and `scripts/run_ask_graph.py` | 15 |

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
