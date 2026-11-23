# 17 — Chat page in the web front end

## What we built

The page is now a conversation. "Chat" is the main tab and the old search page stays as a second tab (hash routing, `#/chat` / `#/search`, no router library). The browser stores only the `thread_id` in `localStorage`; the conversation itself lives in the server's graph state — reload the page and `GET /chat/{thread_id}` puts every turn back, restart the server and a notice says the conversation was not kept and a fresh thread begins. Replies reuse the existing answer components, and a follow-up shows what it was understood as ("Follow-up understood as … — the conversation context was carried over"). The composer sends on Enter, adds a line on Shift+Enter, and disables while a reply is pending; the message list is a `role="log"` live region and everything works by keyboard.

## Concepts

- **Where a conversation lives:** the server's checkpointed graph state owns the turns; the browser keeps only the thread id — like a navigation argument whose backing store the server owns. Visible in `web/src/components/ChatPage.tsx` and `application/chat.py`.
- **Hash routing without a router:** `tabFromHash` in `web/src/tabs.ts`; both tabs stay mounted with the inactive one `hidden`, so switching tabs never loses an in-flight result.
- **Generation counter:** a `useRef` bumped by "New chat" and captured per send, so a stale reply resolving after a reset is discarded instead of resurrecting the cleared conversation — the bug class every optimistic UI has.
- **Intent-level replay:** the chat golden eval no longer executes the register; the capture now stores each turn's summary and the replay rebuilds the rewrite prompt from the cache, byte-identically — visible in `evals/test_chat_golden.py` and the `summaries:` section of `evals/chat_cache.yaml`.
- **Live region:** the message list is `role="log"`, so screen readers announce replies as they land.

## Decisions and why

- **Summaries in the cache, not re-execution.** The rewrite prompt's history includes the providers each earlier turn showed — re-executing that against the 29k-row register cost ~21 minutes per eval run. The capture stores what the model actually saw; the digest keys prove byte-identity (a wrong summary misses the cache loudly, and a test pins that every stored summary is consumed). Alternative rejected: replaying against a seeded mini-register — the providers shown would differ from capture time and every digest would miss.
- **A generation counter over request cancellation.** Aborting the fetch would still race the checkpointer and leave the storage write; a counter makes "this reply belongs to a conversation that no longer exists" a cheap comparison at resolution time.
- **No router library.** Two tabs do not need history entries or a dependency; the hash is the whole contract, and `?q=` keeps landing on Search exactly as before.

## How to say it in an interview

"The chat page is deliberately thin: the browser keeps one string, the thread id, and every turn lives in the server's checkpointed graph state — reloading asks the server for the conversation, restarting the server loses it, and the page says so instead of pretending. The interesting bugs were UI races, not AI: a 'New chat' click while a reply was still in flight would resurrect the cleared conversation and overwrite the fresh thread id, which we fixed with a generation counter and pinned with a test that failed six ways before the fix. And we made the multi-turn eval honest about what it measures: the floors are intent-level, so the replay now rebuilds the rewrite prompt from captured turn summaries and runs in two seconds instead of twenty-one minutes — with a digest check that fails loudly if the cache and the prompt ever drift apart."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. What does the browser store, and what happens on reload and on server restart?
2. Where is a follow-up's rewritten question shown to the user, and why show it?
3. Why did the chat eval get 10,000× faster without changing what it measures?

**Spot it**

4. Where does the page decide which tab to show, and what keeps the search result alive across a tab switch?
5. Which mechanism stops a stale reply from landing after "New chat"?
6. What proves the cached summaries are byte-identical to what the model saw at capture time?

**Defend it**

7. The browser could keep the whole conversation in memory. Defend keeping only the thread id.

**Answers:** 1. Only `thread_id` (localStorage); reload calls `GET /chat/{id}` and restores every turn; restart → `thread_not_found` → a notice that the conversation was not kept and a fresh thread. 2. In the reply's "Understood as" row ("Follow-up understood as …"); showing it makes the rewrite auditable — the user sees exactly what the model carried forward, the same disclosure instinct as the intent chips. 3. The floors are intent-level: the capture stores each turn's summary so the replay rebuilds the rewrite prompt byte-identically from the cache, and the digest keys turn any drift into a loud miss — no register execution needed. 4. `tabFromHash` in `web/src/tabs.ts`; both tabs stay mounted with the inactive one `hidden`, so state (and the one-time `?q=` ask) survives a switch. 5. A generation counter (`generationRef`) bumped by "New chat"/restore and captured per send; both the success and error handlers return early when the generation moved on. 6. The rewrite lookup keys are sha256 digests of the exact system+user prompt — all 15 hitting the committed cache IS the byte-identity proof, and the eval now also asserts every stored summary is consumed. 7. Server-owned state means one source of truth: the same thread continues from any tab, the API's turn caps and reasons (409, 404) stay authoritative, and ticket 20 swaps the storage without touching the page — a browser-owned copy would fork the truth and beg for sync bugs.

## Numbers

- Vitest: 40 tests green (was 22); keyboard-only run-through covered; `pnpm --dir web check` (typecheck, eslint incl. jsx-a11y, prettier, build) green.
- Chat eval: **2.4 s** (was 21–25 min) — floors unchanged: followup_accuracy 13/13 = 1.0, followup_refusal_recall 2/2 = 1.0, zero cache misses, all 28 summaries consumed; the committed rewrite/intent rows are byte-identical (a `summaries:` section was added).
- Default `uv run pytest`: **928 passed / 9 skipped in ~45 s** (register-backed suites deselected as `slow`; the 6 slow tests stay on the pre-push/CI path).
- Reviewer probes reproduced three summaries byte-for-byte from the real register; gates: mypy, lint-imports, stub/private-data checks, schema drift all green.
- Live check (this machine, real Chrome, real register + rule index): first turn answered from the register with locality disclosure; reload restored the conversation; API restart showed the not-kept notice with a fresh thread; error+retry observed during a quota window. **Pending:** one live follow-up (rewrite) turn and a third turn, to be completed when the daily quota resets — covered meanwhile by the eval and the mock-client tests. Worked 2026-11-23 (user `jackson`); first ticket under the leaner process (one PR, one review round).
