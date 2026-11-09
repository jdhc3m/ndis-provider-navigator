# 08 — Evaluation gates, tracing and cost

## What we built

The measurement layer. `evals/thresholds.yaml` owns the numbers — filter accuracy ≥ 0.85, rule hit@3 ≥ 0.7, extraction precision ≥ 0.8 on labelled rows, refusal recall = 1.0 — and every eval test fails below them (lowering a threshold in a probe turns its eval red). Every LLM call site now carries optional Langfuse tracing (`traced(...)`/`flush()` with token and latency metadata; lazy import, no-op without keys). `reports/cost.md` records measured cost and p95 latency per endpoint from the run ledgers, and CI grew a **Model evaluation gate** job that runs the replay-based golden suites from committed caches, plus a prompt-contract test — demonstrated live by a deliberately degraded prompt turning CI red before being closed unmerged.

## Concepts

- **Thresholds as data:** `thresholds.yaml` is the single owned source; evals compare measured vs owned values with explicit messages like "0.840 below the owned gate 0.99" when probed.
- **Label provenance:** every hand-labelled file marks `labelled_by: agent` — nothing is ever implied to be Jackson's judgement; 20 judge-verdict slots exist, 10 agent-labelled, 10 blank for the owner.
- **Prompt contract:** the intent prompt's load-bearing declarations (question-is-data, injection refusal, output shape) are pinned by a test, so editing the prompt in a way that breaks the contract turns CI red even though CI replays cached parses. This is what caught the degraded-prompt demo.
- **Serve-time confidence floor:** `MIN_SERVED_CONFIDENCE = 0.75` — the API withholds claims the model itself scored below 0.75; the record keeps everything. Calibration, not eval-gaming: precision 0.840 served vs 0.732 raw, recall identical (0.905), only false positives removed.
- **Tracing as decoration, not dependency:** Langfuse is a bridge the adapters call; with no keys it costs nothing and tests never notice.

## Decisions and why

- **CI runs what it can, skips what it can't — visibly.** Replay-based evals (intent parses, filter/refusal goldens, prompt contract) run in CI from committed caches; data-backed suites (extraction, retrieval) skip with a workflow notice because their caches are private, while their thresholds stay enforced locally. Alternative rejected: committing derived eval caches — a private-data leak risk.
- **Measured vs estimated, always labelled.** Extraction and judge-run costs are measured from recorded JSONL ledgers ($0.048 each); the pending 300-provider extraction is an estimate with both input and output tokens counted (≈ $1.34); p95 denominators (n=2–4) are stated because tiny samples pretending to be statistics are worse than none.
- **The degraded-prompt demo lives on a scratch branch, not main.** Commit `scratch/degraded-prompt` → CI red on the prompt-contract test (run 42630973402) → draft PR closed unmerged, branch deleted. The damage is reproducible; the bad prompt never lands.
- **The extraction re-run replaced a contaminated cache.** The first calibration mixed two prompt versions; rather than average lies, the labelled run was redone coherently and the old numbers withdrawn.

## How to say it in an interview

"Instead of demos, I made evaluation the gate. Every metric the project cares about — filter accuracy, retrieval hit@3, extraction precision, refusal recall — lives in a thresholds file and fails the build when the measured value drops below it. The interesting CI problem: the expensive evals need private data and a real model, so CI runs the replay-based ones plus a prompt-contract test that pins the load-bearing lines of my prompts — and I proved it works by pushing a deliberately degraded prompt on a scratch branch, watching the eval gate go red, then closing the PR unmerged. For observability, every LLM call site goes through one adapter that emits token and latency traces to Langfuse, and degrades to a no-op without keys. Cost is measured from run ledgers — extraction and judging cost about five cents each — with estimates explicitly labelled, and small sample sizes in latency percentiles admitted rather than hidden."

## Check yourself

1. Why pin prompt wording with a test when CI replays cached model outputs?
2. Why is a serve-time confidence floor honest calibration but an eval-only filter would not be?
3. Why label p95 numbers with their sample size?

**Answers:** 1. Replays prove code correctness against yesterday's outputs; the contract test proves the prompt that would run tomorrow still makes the declarations those outputs depend on — together they cover both halves. 2. The floor changes the product uniformly (weak claims are withheld from users, the record keeps everything, recall is unchanged); an eval-only filter deletes inconvenient datapoints from the measurement while the product still serves them — that's gaming the metric, not improving the system. 3. A p95 over two samples is an anecdote; printing the denominator tells the reader how much weight the number can carry and when a real measurement is owed.

## Numbers

- Measured vs owned: filter accuracy **1.0** (≥ 0.85), refusal recall **1.0** (= 1.0), rule hit@3 **1.0/1.0** (≥ 0.7), extraction precision **0.840 served / 0.732 raw** (≥ 0.8), macro recall 0.905.
- Judge agreement (agent-labelled vs model judge): **7/10 = 0.70**; 10 verdicts await the owner.
- Cost: extraction $0.048 measured (46 calls), judge $0.048 measured (50 calls), intent golden ≈ $0.0056 est., pending 300-provider run ≈ $1.34 est. (in+out). Eval gate itself costs $0 (replays).
- 655 unit/integration tests passed (+21), 8 eval tests passing, 1 designed skip; red-CI run: 42630973402.
