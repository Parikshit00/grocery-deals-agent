# grocery-deals-agent

<!-- <p align="center">
  <img src="animation/architecture.gif" alt="How the system works: agents scan weekly prospekts into a vision model, offers land in a validity cache, and basket search runs over the cache" width="920">
</p> -->

Finds the cheapest current grocery deals across German supermarkets for a shopping list or a
recipe. Each chain's official weekly prospekt is browsed by its own agent and read page by page
with a locally served vision model; everything runs on local hardware, no external LLM APIs.

Supported chains: Lidl, Kaufland, Aldi Sued, Penny, Rewe, Netto.

<!-- demo: add the application walkthrough video (YouTube link or mp4) here -->

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

## Quick start (Docker)

Needs an NVIDIA GPU pair, the NVIDIA container toolkit, and the two model checkpoints on disk.

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent

scripts/download_model.sh Qwen/Qwen3-30B-A3B-Thinking-2507-FP8 /path/to/models/Qwen3-30B-A3B-Thinking-2507-FP8
scripts/download_model.sh Qwen/Qwen3-VL-32B-Instruct-FP8 /path/to/models/Qwen3-VL-32B-Instruct-FP8
scripts/pull_models.sh                                        # verify checkpoints

MODELS_DIR=/path/to/models docker compose -f infra/docker-compose.yml up -d --build
```

UI on http://localhost:28735/, API on :28734. The compose stack runs Postgres, both vLLM model
servers (GPU 0 reasoning, GPU 1 vision), the backend, and the nginx-served UI; model weights are
mounted from `MODELS_DIR`, never baked into images.

### Development (host-run)

```bash
./scripts/setup_env.sh && conda activate angebot_agent
docker compose -f infra/docker-compose.yml up -d postgres
scripts/serve_llm.sh        # reasoning model, GPU 0
scripts/serve_vlm.sh        # vision model, GPU 1
scripts/run_dev.sh          # API on :28734
cd frontend && npm install && npm run dev   # UI with HMR
```

Config lives in `.env`, created from `.env.example`.

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
backend/tests/  unit tests + API tests
frontend/src/
  features/     scan and basket panels
  components/   shared UI (offer card, timeline, chain logos, icons)
  lib/          API client and types
```

Adding a chain: copy `backend/app/agents/lidl.py` to `agents/<retailer>.py`, adapt the
navigation, add `agents/prompts/<retailer>_extract.txt`, and register it in
`agents/__init__.py`.

## License

Apache License 2.0. See [LICENSE](LICENSE).
