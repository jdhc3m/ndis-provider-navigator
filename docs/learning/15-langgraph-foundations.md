# 15 — LangGraph foundations: one question through a graph

## What we built

No user-visible feature: `POST /ask`, the `Ask` use case and the eval gate are exactly as they were. What changed is that the pipeline now exists a second way — as a LangGraph graph. `Ask.__call__` was split into eight named stage functions (parse intent, pre-execution refusal, provider/price/rule sections, evidence, guard, compose) that still compose in a straight line exactly as before; `build_ask_graph(deps)` in `adapters/graphs/` wires those same stages into a graph: `parse_intent` routes to `refuse` or fans out to the three section nodes, which run in parallel in one super-step and join once at `evidence`, then `guard`, then END. A parity eval replays every golden case through both shapes and proves the answers are equal field for field — so tickets 16–24 (memory, pausing, parallel tools, loops) start from a graph that is known to be correct.

## Concepts

- **State:** one dictionary that travels through the graph; a node reads it and returns only the keys it wants to change — `AskState` in `src/ndis/application/ask.py` (a stdlib TypedDict, no langgraph import, so it stays mypy-strict and layer-clean).
- **Node:** a plain function `state -> partial update`; here each is a thin closure in `build_ask_graph` over one stage function — the graph module is pure plumbing, no business rule lives in it.
- **Conditional edge:** a function that looks at the state and picks the arrow — `route_after_parse` returning `"refusal"` or `"ok"`.
- **Super-step:** nodes due at the same time run together as one step — `providers`, `price` and `rule` share one and ran on separate worker threads in the probes (three 0.6 s sleeps finished in 0.63 s wall).
- **Reducer:** when two nodes in the same super-step write the same key, a merge function is mandatory — `steps: Annotated[list[Step], operator.add]` appends; without it LangGraph refuses the double write.
- **Fan-in join:** a node with several incoming edges waits for every source of the same super-step — all three sections run every time (a null section returns an empty update), so `evidence` runs exactly once; pinned by `kinds.count("evidence") == 1` in `tests/test_ask_graph.py`.

## Decisions and why

- **Parity first, features later.** The graph is added beside `Ask`, not replacing it in `POST /ask`; a parity eval (zero model calls, replayed captures) proves equivalence before anything depends on the graph. Alternative rejected: switching the endpoint over now — the value of the graph is the four things coming in tickets 16–24, and switching before proving parity would put the eval gate at risk.
- **Stages in `application`, wrappers in `adapters/graphs`.** The layer rule survives the framework: `AskState` and every business rule live in `application/ask.py`; the graph module imports langgraph and closes over dependencies passed through `build_ask_graph(deps)` — no module globals, nothing for import-linter to catch later. Alternative rejected: graph nodes containing logic directly — that would make the graph module the new monolith.
- **The use case stays the source of truth.** `Ask.__call__` is now the composition of the named stages; the graph reuses the same functions. One behaviour, two shapes, one place to change.
- **`langgraph>=1.2,<2`, nothing else.** No LangChain chat-model package: model calls keep going through the `LlmClient` port, so the vendor rule (model names only in `config.py`/env) still holds; `langgraph`/`langchain_core` were added to the import-linter forbidden lists for `domain` and `application`, and a probe confirms a violation fails the gate.

## How to say it in an interview

"We refactored our question-answering pipeline into LangGraph without shipping a feature: the existing use case was split into named stages, and a graph wires those same stages as nodes — parse, fan out to three parallel sections, join once, guard. The safety net is a parity eval: every golden case is replayed through both the straight-line path and the graph, and the answers must be equal field for field, with steps compared as a set because parallel timing may differ. That cost us about seven minutes of eval time and bought us a proven-correct skeleton for the phase that needs a graph: conversation memory, interrupting the user with clarifying questions, parallel tool calls and evidence loops. We also kept the framework at the edge — model calls still go through our own port, and import-linter forbids langgraph in domain and application."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. What are state, node and conditional edge in this repo's graph?
2. Why must a parallel write to `steps` have a reducer, and what does ours do?
3. How does the parity eval prove the graph is correct without calling a model?

**Spot it**

4. Where does the graph decide to refuse before any section runs?
5. Which test would fail if `evidence` ran twice?
6. Where is the append reducer declared, and why does it live in `application` and not the graph module?

**Defend it**

7. This pipeline alone does not need a graph. Defend adding one now anyway.

**Answers:** 1. State is `AskState` (question, parsed intent, section results, refusal fields, `steps`); nodes are the thin functions inside `build_ask_graph` wrapping the stages; the conditional edge is `route_after_parse` picking `refuse` vs `start_sections`. 2. Two nodes in the same super-step (`providers`, `price`, `rule`) each return a `steps` entry; without a reducer LangGraph raises on the conflicting write; ours is `operator.add`, appending both. 3. It replays the cached intent payloads from `evals/intent_cache.yaml` through both paths with the same fakes, compares every `CombinedAnswer` field, and compares `steps` as a set of `(kind, input_summary, output_summary)` — timing and order may differ, content may not. 4. The conditional edge `route_after_parse` in `adapters/graphs/ask_graph.py`, which routes to `refuse` when `pre_execution_refusal` decides one; the unit test proves via call counters that no section node ran. 5. `tests/test_ask_graph.py` asserts `kinds.count("evidence") == 1` (a set of kinds alone would not catch a second run). 6. On `AskState` in `application/ask.py` as `Annotated[list[Step], operator.add]` — it lives there because state shape is a use-case concern and keeps the graph module free of anything but wiring. 7. Alone it is overkill — that is said openly in the note. But the next nine tickets need memory, interrupts, parallel tools and loops, and refactoring those in later means re-proving behaviour each time against a moving pipeline; doing the mechanical refactor now, while behaviour is frozen and parity is cheap to prove, means every later ticket starts from a graph that is already known-equal to the battle-tested path.

## Numbers

- Parity eval: 35/35 golden cases equal field for field (all 17 `CombinedAnswer` fields; `steps` as a set of triples); zero model calls; ~6:55 with the register loaded; the eval suite is 12 passed / 3 skipped (~12:45).
- Concurrency: 20 invocations × 4 threads against one temp SQLite file, identical answers; probe: three 0.6 s section sleeps in 0.63 s wall on 4 worker threads; `evidence` exactly once per invoke.
- Dependency: `langgraph` 1.2.24; transitive additions only (langchain-core, langgraph-checkpoint/prebuilt/sdk, langsmith, orjson, ormsgpack, jsonpatch/jsonpointer, tenacity, sniffio, distro, requests-toolbelt, truststore, uuid-utils, websockets, xxhash, zstandard); zero pre-existing versions changed; `uv lock --check` green, CI lock step green first try.
- Checks: 871 passed / 15 skipped (was 865/14); lint-imports 2 kept / 0 broken with the new forbidden imports; drift test proven failing and passing.
- Review: APPROVE round 1, 0 must-fix (approval record posted on PR #65); one nit applied before push (`dd033ee`), one carried forward to ticket 16 (checkpointed state must hold JSON-plain values). Worked 2026-11-23 (user `jackson`).
- Live note: the hosted endpoint was quota-exhausted that day (native 429; the compat surface returned an empty 404 in that state), so `scripts/run_ask_graph.py` was verified via `--diagram` and the parity eval; a live one-question run is pending for the demo.
