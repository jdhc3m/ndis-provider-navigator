# 18 — Clarifying questions: pause and resume with interrupt

## What we built

In the chat only, an ambiguous suburb no longer forces the user to ask a whole new
question: the graph stops mid-turn, asks "which Paddington?", and carries on from the
same place when the user picks. A new `precheck_locality` node in the chat graph runs
the pure provider plan (postcode guard plus the deterministic gazetteer lookup,
extracted as `plan_provider_section`); when the resolver reports `locality_ambiguous`
and no stated postcode carries the filter, the node calls `interrupt()` once and the
API answers `status: "needs_input"` with the candidates as buttons. `POST
/chat/{thread}/resume` re-enters the paused node with `Command(resume=option_id)`; a
pure domain validator accepts only an offered option (anything else records a reason
and a conditional edge asks the same question again); the chosen locality and the
pre-parsed intent are handed into the pipeline, which then runs exactly once and
completes the turn. A new message drops the pending question and is answered
normally; a resume with nothing pending is a 409. `POST /ask` is byte-identical
(parity eval green) — the pause exists only where a checkpointer exists.

## Concepts

- **`interrupt(payload)`:** called inside a node, it saves the state, stops the run
  and hands the payload to the caller — here the pending question with its options,
  which the API reads from the snapshot's `task.interrupts`.
- **`Command(resume=value)`:** the caller re-invokes the graph on the same
  `thread_id`; inside the node `interrupt()` now returns `value`. On a thread whose
  turn already completed, a resume is a silent no-op that replays the previous turn —
  the property the API layer must guard (see Decisions).
- **Node re-execution:** on resume the node starts again from its first line, so
  everything before `interrupt()` runs twice. That prelude must be pure — our
  pre-pause work is exactly the postcode guard and the gazetteer lookup, no model
  call and no write (pinned by a test that counts resolver runs and compares the
  LlmClient's request log byte for byte across the pause).
- **Resume values map to interrupts in order of occurrence** (verified against the
  installed langgraph 1.2.24 source): a re-ask loop must therefore create a new task
  — our conditional edge routes back to the node, whose fresh task's `interrupt()`
  pauses again with the reason — never a `while` loop around `interrupt()`.
- **A pause needs a checkpointer:** the ticket-15 ask graph is compiled
  `checkpointer=False` because its state carries page text; so the pause lives in the
  chat graph, whose checkpointed state is JSON-plain only.

## Decisions and why

- **Two-phase pause, not interrupting inside the pipeline.** The inner ask graph
  cannot pause (no checkpointer; a true subgraph would checkpoint page text), and a
  spike on langgraph 1.2.24 measured that an `UntrackedValue` channel write is lost
  across a pause — so lifting the three sections into chat-graph sibling nodes would
  have forced JSON round-trips of every section outcome into checkpointed state.
  Instead the plan runs purely before the pipeline, the pause happens where nothing
  has been spent yet, and the pipeline runs once with the choice pinned. The
  price-section count across pause and resume is exactly 1.
- **Pause only when the answer is blocked.** Ambiguity with a stated postcode keeps
  ticket-13 behaviour — the postcode carries the filter and the suburb is disclosed
  as rejected — because interrupting there would stop a question the data can answer.
- **Choice wins, disclosed.** The chosen locality pins `provider_section_stage`; the
  answer discloses the resolution as the choice that decided it.
- **Validation in `domain`.** `locality_by_option_id` accepts only an offered
  option id, derived purely from the candidate pair (`qld-4064`), so capture-time and
  replay-time ids agree without state.
- **The API check and the resume invoke are one atomic step under the turn lock.**
  The review round found that a check outside the lock lets a second overlapping
  resume pass, then replay the winner's answer (the silent no-op above); under the
  lock the loser re-checks, finds nothing pending, and gets 409. Read-then-act
  checks on shared state cannot hold without the lock — the file's own invariant.

## How to say it in an interview

"Our chat uses LangGraph's human-in-the-loop interrupt. When a suburb is ambiguous,
the graph stops mid-turn and returns 'which Paddington?' with the candidates; the
user's pick re-enters the paused node through the checkpointer. The cost of that
feature is that a resumed node restarts from its first line, so everything before
the pause must be pure and repeat-safe — we moved exactly the postcode guard and the
gazetteer lookup there and pinned 'no model call, no write before the pause' with a
test that diffs the model client's request log across the pause. We also learned the
hard concurrency lesson: a `Command(resume=...)` on a finished turn is a silent
no-op that replays the old answer, so a double-click could duplicate a turn — the
review caught it, and the nothing-pending check and the invoke are now one atomic
step under the same lock."

## Check yourself

Three tiers. **Say it** is the level the interview needs; **Spot it** proves you can
navigate the code; **Defend it** is optional stretch — justify a design decision.

**Say it**

1. What happens when a node calls `interrupt()`, and what does the caller send to continue?
2. Why must the code before `interrupt()` be pure, and what exactly runs twice?
3. How does an invalid option id fail, and where is the validator?

**Spot it**

4. Which node pauses the graph, and why is the pause there and not inside the ask pipeline?
5. Where does the chosen locality enter the ask pipeline, and what does the parse node do on a resumed turn?
6. Which test pins "no model call before the pause"?

**Defend it**

7. Defend pausing the conversation instead of listing the candidates and asking the user to re-ask.

**Answers:** 1. It saves the state with the checkpointer, stops the run and surfaces
the payload as a task interrupt; the caller re-invokes on the same `thread_id` with
`Command(resume=value)`, and the next `interrupt()` call in the resumed node returns
that value. 2. On resume the node re-runs from its first line, so the prelude runs
twice: writes would double and model calls would double-spend — ours is the postcode
guard plus the pure gazetteer lookup. 3. `locality_by_option_id` in
`domain/locality.py` accepts only an offered option id; anything else is recorded as
a reason in state and the conditional edge routes back to the node, which interrupts
again with the reason — a new task, not a loop. 4. `precheck_locality` in
`adapters/graphs/chat_graph.py`; the ask graph is compiled `checkpointer=False`
(page-text rule) so it cannot pause, and a true subgraph would checkpoint page text.
5. As `chosen_locality` in the chat state, rebuilt into a `Locality` by the `ask`
node and passed to `provider_section_stage(chosen_locality=...)`; the parse node
skips its model call when a pre-parsed intent is handed in. 6.
`test_the_paused_prelude_reruns_purely_on_resume` — the resolver call count and the
byte-identical `LlmClient` request log across the pause. 7. Re-asking throws away
the rest of the turn — a price query or rule question riding along with the suburb
would be re-parsed and re-answered from scratch, and the user re-types; the pause
preserves the turn and costs nothing: the node's pre-pause work is pure, the model
has not run, and the pipeline executes exactly once after the choice.

## Numbers

- Chat graph: `START → {first_turn: parse_intent, follow_up: contextualise →
  parse_intent} → precheck_locality → {reask: precheck_locality | answer: ask} → ask
  → remember_turn`; diagram regenerated, drift test green.
- Price-section executions across pause + resume: **1** (0 at pause, 1 after resume),
  measured with a counting repository; model calls across the whole pause: 1 (the
  pre-pause intent parse; resume makes none — parse handed in pre-parsed).
- Checks: 960 passed / 1 skipped (was 903/1 after ticket 17); coverage gate green;
  ruff/mypy/lint-imports green; eval `17 passed / 1 skipped` (~11–12 min with the
  register); web 47 Vitest tests + build; `schema.d.ts` byte-identical after regen.
- Captures: `evals/chat_cache.yaml` +6 payloads (two golden conversations,
  `ambiguous_place_choice`, `ambiguous_place_with_price` in `evals/chat_golden.yaml`),
  recorded through the real endpoint with no 429s; floors byte-identical.
- Review: round 1 REQUEST_CHANGES with 1 must-fix (the resume lock race, above) →
  fixed → scoped re-verification green; nits recorded as STATE follow-ups. The
  reviewer's live probe reproduced: ambiguous → needs_input, concurrent resumes →
  exactly one 200 + one 409, GET served throughout, 409 `nothing_pending` sequential.
- Pending (quota): the live browser check of one ambiguous turn through the built
  page — the daily quota died during the review probe; the graph/API behaviour it
  exercises is covered by the live-server probe above.
