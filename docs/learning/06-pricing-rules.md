# 06 — Pricing rules RAG

## What we built

The rule-answering pipeline. `scripts/fetch_public_data.py` downloads the Pricing Arrangements and Price Limits 2025-26 (v1.1, 104 pages — the written rules; the 2026-27 "schedules" on the site are tables only), `pymupdf` turns pages into text with page numbers preserved, and two chunking strategies index it: fixed 600-token and heading-aware (from the 147 TOC headings). Chunks are embedded with `gemini-embedding-001` through the ticket 05 adapter (now with an `embed` method sharing the same pacing/backoff/budget), stored in a `sqlite-vec` table, and served by `POST /ask-rule`: a grounded answer with (page, quoted-span) citations, or `refused: true` with a reason. On the 10-question golden set both strategies score hit@3 = 100%.

## Concepts

- **Chunking strategies:** fixed 600-token windows vs heading-aware sections; both built by the same chunker parameterised by strategy so retrieval can compare them fairly. `adapters/pricing_pdf.py`, `domain/pricing.py`.
- **Embeddings and cosine similarity:** text → 3072-dim vectors, unit-normalised, so cosine = `1 − d²/2` in sqlite-vec's distance — verified in a test against a plain domain function. `adapters/rule_vector_store.py`.
- **Two-gate refusal:** retrieval gate (top similarity < 0.65 → refuse before any LLM call) plus a judge gate (the model must say the draft answer is supported by the cited chunks, else refuse). `application/pricing.py`.
- **Citation determinism:** every cited quoted span must be a verbatim substring of the chunk that came from the cited page — the same "LLM proposes, domain disposes" rule as ticket 05, now applied to answers. `domain/pricing.py`.
- **Embedding cache:** content-hash keyed (`sha256(model, text)`), so a rebuild after text changes never serves a stale vector. `adapters/embedding_cache.py`.

## Decisions and why

- **2025-26 PAPL, not the 2026-27 "schedules".** The live page now offers pricing schedules (tables only); the written rules Jackson's demo questions need are in the 2025-26 PAPL v1.1. Documented in the README; version tracking is a listed limitation.
- **`sqlite-vec` over Chroma.** The vector index lives in the same SQLite file family the stack already uses — no server, no new operational surface. `pymupdf` pinned `>=1.25,<2` (2.x has no x86_64 macOS wheel here).
- **Refusal fires before the LLM when similarity is low** — cheap, deterministic, and testable without a key; the judge is only spent on plausible retrievals.
- **`urllib` with a browser UA, not curl/httpx, for the PDF.** This machine's curl fails Cloudflare's TLS fingerprint check; the existing fetch pattern already passes it.
- **The Reviewer caught a gate bug, not a code bug:** `pytest -m eval` exited 1 because the repo-wide coverage gate applied to the eval-only subset; the gate command everywhere is now `pytest -m eval --no-cov` (the convention CI already used).

## How to say it in an interview

"For pricing-rule questions I built a small RAG pipeline with honest refusal. The Pricing Arrangements PDF becomes page-tagged chunks under two strategies — fixed 600-token and heading-aware — embedded into sqlite-vec inside our existing SQLite database, no separate vector service. When you ask a rule question we retrieve the top chunks and only spend an LLM call if the best similarity clears a threshold measured from the golden set (0.65, with real hits at 0.71–0.88 and off-topic at 0.50–0.62); then a second gate makes the model confirm the answer is actually supported before we return it. Every citation is a page number plus a verbatim quoted span checked by pure domain code, and an off-topic question is refused without touching the model. Both chunking strategies hit 100% hit@3 on the golden set — and the eval that proves it runs in CI from a cached index, with the same code path as the report."

## Check yourself

1. Why unit-normalise the embedding vectors?
2. Why two refusal gates instead of one?
3. Why is the embedding cache keyed by a hash of model *and* text?

**Answers:** 1. With unit vectors, squared Euclidean distance converts exactly to cosine similarity (`1 − d²/2`), which is what sqlite-vec's vec0 tables compute — no approximation, and it is testable against a plain function. 2. The similarity gate is cheap, deterministic and model-free (it must catch off-topic input without spending quota), while the judge gate catches the case where retrieved chunks are on-topic but don't actually support the draft answer. 3. Embeddings are model-specific: the same text under a different model (or model version) yields an incompatible vector, so the model name must be part of the cache key.

## Numbers

- hit@3: fixed 100% (10/10), heading-aware 100% (10/10); golden top-1 similarity range 0.71–0.88, off-topic max 0.620, threshold 0.65.
- Corpus: 104 pages → 175 fixed / 284 heading-aware chunks; 3072-dim embeddings; 409 cached vectors; index 26 MB.
- 549 unit/integration tests passed (+83), 2 eval tests passing (extraction + rule retrieval), 1 designed skip; zero private data tracked.
