# Three-minute recording checklist

For a 3-minute screen recording of the demo in `docs/demo.md`. Timings add up to ~170 s,
leaving slack for cuts. Written after the 2026-11-10 live walkthrough.

## Prepare beforehand

- [ ] Serve for real: `set -a; source ~/.config/ndis/.env; set +a` then
      `uv run uvicorn ndis.api.app:app --port 8000` — with `data/` loaded (register,
      catalogue, centroids, rule index, private extraction/pages for the evidence shot).
- [ ] `pnpm --dir web build` (restart uvicorn afterwards). The page must be the one
      FastAPI serves at `http://localhost:8000/` — **not** `VITE_MOCK_API=1`; fixtures
      must never appear in the recording.
- [ ] Browser fullscreen at 1920×1080 (or the largest clean window), zoom 100%, dark or
      light theme as you like; close other tabs; hide bookmarks bar; do-not-disturb on.
- [ ] Second desktop/space with a terminal already in the repo root, env sourced, and a
      `curl` snippet pasted into the command line for the raw rule answer (step C4).
- [ ] **Start the eval gate just before recording**: `uv run pytest -m eval --no-cov`
      takes ~95 s with the register loaded. Start it as you hit record; it finishes
      during shot D and you show its summary there. (Full gate measured 2026-11-10:
      8 passed, 1 skipped, ~97 s.)
- [ ] Optionally pre-run every question once so you know what each answer looks like.
- [ ] Screen recorder: 1080p, capture system audio off (you narrate), record at 30 fps.
      Do a 10 s test clip and check the terminal text is legible.

## Shot list

| Shot | Time | Screen | Do | Say |
| --- | --- | --- | --- | --- |
| A1 | 0:00–0:10 | Browser | Page at `/`, hover nothing, let the header breathe. | "This is a question-and-answer service over the real NDIS provider register, the official support catalogue and the published pricing rules. The header — snapshot date, catalogue, pricing document, model — comes live from the API." |
| A2 | 0:10–0:40 | Browser | Ask the brief question verbatim: `registered OT for a 7-year-old near 4101 who does home visits, and what's the hourly price limit?` Point at the intent chips, then the **Not answered** banner. | "I ask it the question I actually have. The model's only job is to parse intent — and the system refuses the part it must not do: picking a provider for my child. Note the parse is transparent: what it understood, and the part it dropped because no support item was named." |
| B1 | 0:40–1:25 | Browser | Ask: `Which approved providers have an outlet in postcode 4120, and what is the price limit for 15_056_0128_1_3 in Queensland?` Scroll: intent chips → provider cards with distances and their `exact`/`postcode_centroid` labels → the card with verified services; open one evidence disclosure; land on the price card. | "Every provider here is a register fact with its straight-line distance and where that number came from. Service claims are only shown with the verbatim quote from the provider's own website — and the caveat that the register doesn't record profession. The price limit is looked up in the catalogue, not generated." |
| B2 | 1:25–1:45 | Browser | Open **How this answer was built**; point at rows and the cost column. | "One model call in this whole answer — the parse. Everything downstream is deterministic, and every step discloses its time, tokens and cost." |
| C1 | 1:45–2:10 | Browser | Ask: `Can a provider charge for a short-notice cancellation?` Show the citation: page 26, quoted span, "every citation verified on its page". | "Rule answers must cite a page, and the citation check — a verbatim span of that page — is pure code, not a second model opinion." |
| C2 | 2:10–2:25 | Terminal | Switch desktop, press Enter on the prepared `curl` of `/ask-rule`; highlight `"page": 26` and the quote. | "Same answer over the raw API — citations are part of the response contract, not page decoration." |
| D1 | 2:25–2:45 | Terminal | The eval gate summary has landed: `8 passed, 1 skipped` (+ the skip line about owner verdicts). | "Quality floors are enforced by an eval gate that replays committed captures — no quota, no trust-me numbers. Filter accuracy, rule retrieval, extraction precision, refusal recall." |
| D2 | 2:45–3:00 | Browser or docs | Open `docs/learning/LEARNING_LOG.md` (or the README results table). | "One note per ticket — decisions, rejected alternatives, measured numbers." |

If you have 10 spare seconds, add the refusal shot (recommendation + prompt injection,
both refuse with a machine reason) between C1 and D1; it is the strongest 15 seconds of
the demo.

## If something fails while recording

- **502 / "endpoint failed after 4 attempts"**: the free tier throttled. Keep recording;
  click **Try again** in the error state and narrate recovery ("this is the hosted
  endpoint throttling; the service surfaces the failure instead of faking an answer").
  If the quota is out for the day, switch `LLM_MODEL` to the lighter fallback recorded in
  `docs/harness/STATE.md` (tickets 05–06) and restart uvicorn — the rule index stays
  valid because embeddings are keyed separately.
- **Terminal fallback**: before recording, capture the terminal outputs you would have
  shown live (`curl /ask` answers, the eval gate summary) into a file, and if the live
  call fails on camera, cut to the pre-captured terminal — and say so in the narration.
  Never splice in a state the system did not produce.
- **Page fails to load**: `/` 404s when `web/dist` is missing — rebuild and restart
  uvicorn before recording, not during.
- The mock mode is not a fallback: fixtures are labelled as such in the UI, and using
  them in a recording would misrepresent the system.

## After recording

Check the clip for: legible terminal text, no secrets on screen (the env is sourced in
the shell, never `cat`ed), no private paths under `data/private/` visible in any file
listing, and the model name only appearing where the page's own header shows it (that is
the API's `/meta` doing its job).
