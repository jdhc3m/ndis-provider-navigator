# Deployment plan — Fly.io path

Decided 2026-11-24 with the owner. Tickets 25–33 are issues #75–#83
(label `ticket`). Supersedes the earlier VPS sketch; the Caddy/DuckDNS
ticket was deleted because Fly provides TLS certificates for `*.fly.dev`.

## Locked decisions

- **Platform:** Fly.io (cheap-VPS path was the fallback; the single-process
  + SQLite + in-RAM-chat architecture fits Fly's one-machine-with-volume
  model exactly, and rollback makes the demo narrative).
- **Audience:** fully public — Epic B tickets are launch blockers, not
  follow-ups.
- **Domain:** `*.fly.dev` for launch; a custom domain is a ~30-minute
  add-on later (see bottom).
- **Data:** derive-and-ship. The scraper (`collect_pages`) and extraction
  are build-time pipelines on the Mac; the server only reads materialized
  files. Zero LLM spend at deploy. Nothing under `data/private/` enters
  git (`scripts/check_private_data.py`); `data/private/cost/` stays home.
- **Spend cap:** provider-side budget alert \$10 / hard cap \$15 per month
  on the Gemini key, plus an app-level deterministic daily guardrail
  (ticket 29) so a bug or abuser cannot outrun the console cap.
- **Done means:** HTTPS URL reachable, full gate chain + slow eval gate
  green, smoke tests pass.

## Architecture on Fly

One machine (`shared-cpu-1x`, 512MB, region `syd`), one persistent volume
(`ndis_data` at `/data`) holding the SQLite databases, the register CSV and
the page-text/extraction artifacts. `uvicorn` runs a single worker: the
chat threads live in RAM and SQLite forbids multi-writer scale-out — this
is a feature of the design, disclosed via `GET /meta`. Deploys and
`suspend` restarts lose conversations; that is the documented behaviour.

Runtime file layout on the volume mirrors the repo layout:

```
/data/ndis.db                      (register + catalogue + postcodes + extraction claims)
/data/rule_index.db                (sqlite-vec pricing-rule chunks)
/data/public/register.csv          (register snapshot date source)
/data/private/pages/               (page text; verbatim quote re-verification reads it live)
/data/private/extraction/          (per-provider extraction cache)
/data/private/embedding_cache.db   (content-hash-keyed embedding cache)
/data/private/maps_brisbane.db     (geo ranking, read-only)
```

Env wiring (`fly.toml`): `NDIS_DB_PATH`, `RULE_INDEX_PATH`,
`REGISTER_CSV_PATH`, `EMBEDDING_CACHE_PATH`, `EXTRACTION_DIR`, `PAGES_DIR`.
Secrets (`fly secrets`): `LLM_BASE_URL`, `LLM_MODEL`, `EMBED_MODEL`,
`LLM_API_KEY`, optional Langfuse keys. Never in git, never in `fly.toml`.

## Tickets

### Epic A — private URL up

| Ticket | Issue | Scope | AC |
|---|---|---|---|
| 25 Fly scaffold | #75 | Multi-stage Dockerfile (pnpm builds `web/dist`; uv + python3.12-slim runtime) + `fly.toml` | image builds locally |
| 26 Data to volume | #76 | One-time push of the ~145MB artifacts (script), exclude `cost/` | API paths resolve on the volume |
| 27 Secrets | #77 | `fly secrets set` for the LLM/Langfuse envs | `/meta` reports `configured=true` |
| 28 First deploy + smoke | #78 | `fly launch`, volume create, deploy, smoke `/meta` + `/ask` + `/chat` | HTTPS URL serves the app |

### Epic B — public hardening (all launch blockers)

| Ticket | Issue | Scope | AC |
|---|---|---|---|
| 29 Spend cap | #79 | \$15 provider cap + app-level daily guardrail → 429 | overuse trips deterministically |
| 30 Rate limiting | #80 | per-IP limits on `/ask` + `/chat` | burst gets 429s, real use unaffected |
| 31 Abuse basics | #81 | body caps, threads-per-IP cap, bounded logs | abuse gets clean errors |

### Epic C — pipeline + launch

| Ticket | Issue | Scope | AC |
|---|---|---|---|
| 32 CI/CD | #82 | Actions: quality gate → image → `fly deploy` on merge; rollback runbook | deploy ~60–90s, rollback <15s |
| 33 Launch gate | #83 | slow evals once, README runbook, record the clip | owner declares live |

## Runbook (ticket 28's happy path)

```bash
brew install flyctl && fly auth login
fly launch --no-deploy --region syd            # from the repo root, picks up fly.toml
fly volumes create ndis_data --size 3 --region syd
fly secrets set LLM_BASE_URL=... LLM_MODEL=... EMBED_MODEL=... LLM_API_KEY=...
scripts/push_data_to_fly.sh                    # ticket 26 deliverable
fly deploy
```

Deploy-time facts to keep honest in the demo clip: build+release is
minutes; *machine swap* is seconds. Claim "deploy ~60–90s, rollback <15s",
not "N seconds".

## Branch/deploy strategy

Trunk-based, no `develop`. `ticket/NN-slug` → PR → `main`; `main` is
always deployable and only `main` deploys (ticket 32 gates deploys on the
quality job). A long-lived `develop` would add merge overhead and drift
for a solo repo without adding safety. Deployed releases are identified by
git history + `fly releases`; `fly rollback` is the incident path.

## Custom domain later (~30 min)

1. Buy the name (~A$15/yr) at any registrar.
2. `fly certs add <domain>`; add the CNAME/A records Fly prints.
3. Let's Encrypt issuance and renewal are automatic; no code changes — the
   front end is same-origin, so no CORS or URL changes either.
