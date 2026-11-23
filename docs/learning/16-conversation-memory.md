# 16 — Conversation memory: threads, checkpoints and follow-up questions

## What we built

A conversation now survives across questions. `POST /chat` takes a message (and, from the second turn on, a `thread_id`) and returns the server-issued thread id, the standalone question the follow-up was understood as, whether it was rewritten, and the full answer; `GET /chat/{thread_id}` returns the turns. A follow-up like "only the approved ones" is rewritten into "Which of the providers in postcode 4101 are approved?" by a fifth model call site **before** the existing intent parser sees it — so `INTENT_SYSTEM_PROMPT`, its 35 captures and its eval floors are untouched. The rewrite is guarded by a total parser in `domain` (non-empty, ≤ 500 chars; anything else falls back to the raw message with a machine reason the client can see), the history enters the prompt as data and never instructions, and refusals cannot be laundered: a follow-up recommendation request is still refused `decision_request`, an injected instruction is still contained. Memory is one in-process `InMemorySaver`, on purpose: restart the server and every conversation is gone, and the API says so via `GET /meta` (`chat_memory: "in_memory"`).

## Concepts

- **Checkpointer:** the object handed to `compile()` that saves state after every super-step — one `InMemorySaver` per process, created in `api/dependencies.py` and shared by all requests; a fresh saver knows no thread, which is the restart limit made testable.
- **Thread id:** the only thing the browser remembers — `{"configurable": {"thread_id": ...}}`; same id loads the saved conversation, a new id starts empty; server-issued UUID4, client ids validated (422) and canonicalised, unknown → 404 `thread_not_found`, turn 51 → 409 `thread_full`.
- **Subgraph:** the ticket 15 ask graph used as one node of the chat graph — but compiled with `checkpointer=False`, because the inner state carries page text and the outer checkpointer would otherwise save it under a subgraph namespace (verified against the locked langgraph source: inheritance happens through the run config, and `False` refuses any checkpointer, own or inherited).
- **Reducer on a list (memory):** `turns: Annotated[list[ChatTurn], operator.add]` in `ChatState` — each turn appends; JSON-plain only, so a checkpoint never holds anything but what the API itself returns.
- **Rewrite-then-parse:** the follow-up is turned into a standalone question first; the intent parser stays single-question and unchanged — visible in `application/chat.py` (`contextualise_stage`) and `domain/chat.py` (`parse_standalone_question`).

## Decisions and why

- **Rewrite first, parse second.** Re-parsing with the history in the prompt would change `INTENT_SYSTEM_PROMPT`, invalidate all 35 captures and risk the existing floors; a separate rewrite call keeps the proven single-question parser exactly as measured. Alternative rejected: feeding history into the intent prompt — one prompt change re-prices every capture and every floor.
- **Ephemeral ask subgraph.** The ask graph runs inside the chat graph without a checkpointer, so page text never reaches a checkpoint; only the outer `turns` state (JSON-plain) is saved. Alternative rejected: stripping page text out of the ask state — that would reshape a proven pipeline to serve the memory layer.
- **In-memory storage, on purpose.** Its limit (restart loses everything) is visible in the API and the README, which is the point of the ticket; ticket 20 replaces it with Postgres.
- **A lock around the turn cap.** The 409 check and the append are check-then-act across FastAPI worker threads and `InMemorySaver` is not internally locked — a module-level `threading.Lock` serialises one conversation's turns; the reviewer's barrier-aligned regression test failed 6/6 runs on the unfixed code.

## How to say it in an interview

"Multi-turn is one new model call, not a new parser: before the intent parser sees a follow-up, a separate call rewrites 'only the approved ones' into a full standalone question using the last three turns as data — and that output goes through a total parser in the domain layer, so a bad rewrite falls back to the user's raw words with a machine reason instead of a crash. Memory is LangGraph's checkpointer with one thread id per conversation, saved after every super-step; the interesting bug class was checkpoint hygiene — the inner graph carries page text, so we proved against the library source that `checkpointer=False` keeps the inner state out of the checkpoints entirely, and wrote a test that plants a marker in the page text and greps every stored checkpoint for it. And because two requests on one conversation run on different threads, the 50-turn cap is enforced under a lock — our reviewer proved the race before we fixed it."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. What does the model do on turn 1 versus turn 2 of a conversation, and what reaches the intent parser?
2. What happens when the rewrite model returns garbage, an empty string, or a 600-character question?
3. Why does restarting the server lose every conversation, and where does the API admit it?

**Spot it**

4. Where is it proven that page text never reaches a checkpoint?
5. Which test catches two clients pushing a conversation past 50 turns at the same moment?
6. Where does the rewrite cache key come from, and what does a prompt edit do to the cache?

**Defend it**

7. Defend "rewrite into a standalone question" over "give the parser the history".

**Answers:** 1. Turn 1: one model call (the intent parse of the raw message). Turn 2: two — `contextualise` rewrites the message into a standalone question from the last three turn summaries, and it is that standalone question, not the raw words, that reaches the intent parser. 2. The total parser in `domain/chat.py` rejects each with its own machine reason (`rewrite_not_json`, `rewrite_empty`, `rewrite_too_long`, …) and the turn proceeds on the user's raw message, disclosed as `rewritten: false` — a failed rewrite cannot smuggle history past the guards because the narrowed intent it produces carries filters the raw message never stated, which the deterministic guards then drop. 3. The checkpointer is a single in-process `InMemorySaver` with no persistence — by design, so the limit is visible; `GET /meta` reports `chat_memory: "in_memory"` and the README states the limit. 4. `tests/test_chat_graph.py::test_no_page_text_is_checkpointed` plants a marker in a fetched page, runs two turns, dumps every checkpoint tuple's channel values across all namespaces and asserts the marker is absent — it works because the ask graph is compiled `checkpointer=False` in `chat_graph.py`. 5. `tests/test_api_chat.py::test_two_concurrent_posts_cannot_push_a_thread_past_the_cap` — two barrier-aligned TestClient threads; it failed 6/6 runs before the lock existed. 6. From a digest of the exact system+user prompt the model saw (`rewrite_payload_key` in `adapters/chat_golden.py`); editing the prompt changes every digest, so the eval fails loudly on missing payloads instead of replaying stale rewrites. 7. Re-parsing with history would put the conversation into the one prompt whose behaviour the whole eval gate pins — 35 captures, prompt-contract strings, four floors — and every tweak would re-spend a daily quota; a separate rewrite call keeps the measured single-question parser frozen, gives the failure a total domain check and a machine reason, and its own two floors (`followup_accuracy`, `followup_refusal_recall`) measure exactly the new behaviour.

## Numbers

- Golden: 13 conversations / 24 turns (15 follow-ups ≥ 10 pin, 2 refusals = the ≥ 2 pin) — **followup_accuracy 13/13 = 1.0** (floor 0.85), **followup_refusal_recall 2/2 = 1.0** (floor 1.0); first turns 13/13 exact; zero cache misses; `evals/chat_cache.yaml` = 12 rewrite + 31 intent payloads, captured with the `LLM_MODEL` env fallback model (quota), no model name committed.
- Checks: 923 passed / 18 skipped (was 871/15); mypy, lint-imports, stub/private-data guards, `pnpm --dir web check` (22 Vitest tests) and the schema-drift check all green; no new dependencies.
- API surface: `POST /chat`, `GET /chat/{thread_id}`, `GET /meta` + `chat_memory`; 422/404/409 semantics tested including two racing posts.
- Review: round 1 REQUEST_CHANGES — the must-fix was the 50-turn cap race (fixed `bf980eb`, red 6/6 before the fix); round 2 APPROVE. Two defects found after the review while running the real capture: the replay wired a 5-provider page while the capture ran at the served 20 (providers shown are part of the rewrite digest — every provider-bearing follow-up missed the cache) and one golden expectation was stricter than the system's own combined filters+price convention; both fixed (`9aef60e`, `e926871`), measured floors 1.0/1.0 after. Worked 2026-11-23 (user `jackson`).
