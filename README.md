# grocery-deals-agent

Finds the cheapest current grocery deals across German supermarkets for a shopping list or a
recipe. Each chain's official weekly prospekt is browsed by its own agent and read page by page
with a locally served vision model; everything runs on local hardware, no external LLM APIs.

Supported chains: Lidl, Kaufland, Aldi Sued, Penny, Rewe, Netto.

## How it works

**Scan.** Pick chains and a postcode, hit Launch. One agent per chain opens the retailer's
official prospekt in headless Chromium, dismisses cookie and promo popups, and pages through the
leaflet. Captured pages stream into Qwen3-VL while browsing continues; each agent's browser is
visible live in the UI. Extracted offers are cached in Postgres per (retailer, region) with the
prospekt's own validity window, so a chain is scanned once per week and served from cache after
that. Partial or degraded scans never overwrite a good cache row.

**Basket.** Type a shopping list, or a dish in Recipe mode - the reasoning model breaks it into
ingredients (its thinking streams live into the UI), items are matched against the cached offers
by embedding similarity, and the optimizer builds the cheapest basket, either split across stores
or from the single best store. Basket search reads only the cache; it never triggers a scan.

## Stack

- Backend: FastAPI, LangGraph pipeline (resolve -> plan -> retrieve -> optimize), SSE streaming,
  Playwright, PostgreSQL.
- Models, all local via vLLM's OpenAI-compatible API: Qwen3-30B-A3B-Thinking (reasoning, GPU 0,
  `:28800`), Qwen3-VL-32B-Instruct (vision, GPU 1, `:28801`), bge-m3 embeddings
  (sentence-transformers).
- Frontend: React + Vite + TypeScript, framer-motion, plain CSS.

## Quick start

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent
./scripts/setup_env.sh && conda activate angebot_agent

docker compose -f infra/docker-compose.yml up -d   # Postgres

scripts/serve_llm.sh                               # reasoning model, GPU 0
scripts/serve_vlm.sh                               # vision model, GPU 1

cd frontend && npm install && npm run build && cd ..
scripts/run_dev.sh                                 # API on :28734, serves the built UI
```

Open http://localhost:28734/. Model checkpoints are read from a local directory (override with
`LLM_MODEL_PATH` / `VLM_MODEL_PATH`; fetch with `scripts/download_model.sh`). Config lives in
`.env`, created from `.env.example`.

## API

| Method | Path                            | Description                                      |
| ------ | ------------------------------- | ------------------------------------------------ |
| POST   | `/api/prospekt`                 | Scan chains `{retailers, location}` (SSE)        |
| GET    | `/api/prospekt/cache`           | Per-chain cache state for a location             |
| DELETE | `/api/prospekt/cache/{retailer}`| Clear one chain's cached prospekt                |
| POST   | `/api/search`                   | Cheapest basket for a list or recipe (SSE)       |
| GET    | `/api/profile/{id}`             | Last location and recent searches                |
| GET    | `/health`, `/readyz`            | Liveness and readiness probes                    |

## Layout

```
backend/app/
  api/          HTTP routes (SSE streaming endpoints)
  graphs/       LangGraph orchestration (search pipeline)
  agents/       one Playwright agent per chain + shared helpers + extraction prompts
  services/     domain logic: scan orchestration, matching, basket optimizer, geo
  clients/      model clients: reasoning LLM, vision VLM, embeddings
  persistence/  SQLAlchemy models, repository, session
  schemas/      Pydantic API/domain schemas
  core/         config, logging
backend/tests/  unit tests + API smoke test
frontend/src/
  features/     scan and basket panels
  components/   shared UI (offer card, timeline, chain logos, icons)
  lib/          API client and types
```

Adding a chain: copy `backend/app/agents/lidl.py` to `agents/<retailer>.py`, adapt the
navigation, add `agents/prompts/<retailer>_extract.txt`, and register it in
`agents/__init__.py`.

## Notes

Local/LAN use: the API binds `0.0.0.0` with open CORS and no auth; restrict CORS and put it
behind an authenticating proxy before exposing it publicly. Offer data comes from each retailer's
official site; respect their terms of service and `robots.txt`. Secrets live only in `.env`
(gitignored).

## License

Apache License 2.0. See [LICENSE](LICENSE).
