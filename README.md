# grocery-deals-agent

> Finds the cheapest current grocery deals across German supermarkets for a shopping list or a
> recipe, reading each chain's official weekly prospekt with a locally-served vision model.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![React](https://img.shields.io/badge/UI-React%2BVite-61dafb)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

## Overview

Give it a postcode and a shopping list or recipe. The agent browses each supermarket's official
online prospekt, reads the leaflet pages with a local vision-language model (Qwen3-VL), matches the
extracted offers to your items by embedding similarity, and builds the cheapest basket, either at a
single store or split across stores. All reasoning, vision, and embedding models run locally; the
agent path uses no external LLM API.

Supported retailers: **Lidl, Kaufland, Aldi Süd, Penny, Rewe, Netto**. Each is a small recipe under
`backend/app/sources/prospekt/`; extracted offers are cached per region for the prospekt's validity
week.

## Stack

- **Backend:** FastAPI + LangGraph agent (resolve, plan, retrieve, optimize), SSE progress.
- **Models (local, OpenAI-compatible vLLM):** reasoning `Qwen3-30B-A3B-Thinking` on `:28800`,
  vision `Qwen3-VL-32B-Instruct` on `:28801`; embeddings `bge-m3` (sentence-transformers).
- **Browsing:** deterministic Playwright per retailer (Chromium); the VLM does all offer extraction.
- **Data:** PostgreSQL (offer cache) + Redis. **Frontend:** React + Vite + TypeScript.

## Quick start

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent
./scripts/setup_env.sh && conda activate angebot_agent

docker compose -f infra/docker-compose.yml up -d        # Postgres + Redis

scripts/serve_llm.sh   # reasoning LLM on GPU 0 (:28800)
scripts/serve_vlm.sh   # vision VLM on GPU 1 (:28801)

cd frontend && npm install && npm run build && cd ..     # API serves the built UI
scripts/run_dev.sh                                       # FastAPI on :28734
```

Open http://localhost:28734/. FP8 model checkpoints are served from a local directory (override with
`LLM_MODEL_PATH` / `VLM_MODEL_PATH`; pull with `scripts/download_model.sh`); config is `.env`
(created from `.env.example`).

## API

| Method | Path                | Description                                    |
| ------ | ------------------- | ---------------------------------------------- |
| POST   | `/api/prospekt`     | Scan a retailer's prospekt `{retailer, location}` (SSE) |
| POST   | `/api/search`       | Cheapest basket for a list/recipe (SSE)        |
| GET    | `/api/profile/{id}` | Last location and recent searches              |
| GET    | `/health`           | Liveness probe                                 |

## Notes

Local/LAN use: the API binds `0.0.0.0` with open CORS and no auth; restrict CORS and put it behind
an authenticating proxy before exposing it publicly. Offer data comes from each retailer's official
site; respect their terms of service and `robots.txt`. Secrets live only in `.env` (gitignored).

## License

Apache License 2.0. See [LICENSE](LICENSE).
