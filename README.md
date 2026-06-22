# grocery-deals-agent

> Agentic AI assistant that finds grocery discounts, optimizes shopping lists, and searches supermarket offers in Germany using LLM agents.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![React](https://img.shields.io/badge/UI-React%2BVite-61dafb)
![Lint](https://img.shields.io/badge/lint-ruff-261230)
![License](https://img.shields.io/badge/license-Apache--2.0-green)
![Status](https://img.shields.io/badge/status-alpha-orange)

## Overview

Given a location (postcode or address) and either a shopping list or a recipe, the agent finds
nearby supermarkets (Penny, Rewe, Kaufland, Lidl, Aldi, Netto, Edeka, and others), retrieves their
current weekly offers, matches them to the requested items, and builds the cheapest basket, either
at a single store or split across stores. All reasoning, vision, and embedding models run locally;
the agent path uses no external LLM API.

## Features

- Location-aware search from a postcode or address.
- Shopping-list or recipe input; recipes are decomposed into ingredients by a local LLM.
- Semantic matching (embeddings) so "butter" finds butter, not buttermilk.
- Single-store and cross-store basket optimization with totals and savings.
- Long-term memory: remembers your last location and recent searches.
- Tiered, config-driven acquisition with a Postgres cache and scheduled background refresh.

## Architecture

A FastAPI service hosts a LangGraph agent (resolve location, plan items, retrieve offers, build
baskets). Tools are exposed by MCP servers (browse-search, geo). Offers are cached in PostgreSQL and
progress is streamed to a React UI over SSE. See [docs/architecture.md](docs/architecture.md),
[docs/data-sources.md](docs/data-sources.md), and [docs/adr/](docs/adr/).

## Tech stack

| Layer     | Technology                                                       |
| --------- | ---------------------------------------------------------------- |
| Agent     | LangGraph                                                        |
| Tooling   | MCP servers (browse-search, geo)                                 |
| Backend   | FastAPI, Pydantic, SQLAlchemy                                    |
| Data      | PostgreSQL, Redis                                               |
| Models    | Ollama (reasoning + vision), sentence-transformers (embeddings); vLLM optional |
| Frontend  | React, Vite, TypeScript                                         |
| Quality   | Ruff, mypy, pytest, Docker                                      |

## Getting started

### Prerequisites

- Python 3.11+ and Conda (on `PATH`, or set `CONDA`)
- Docker and Docker Compose
- Ollama, with a chat model (default `qwen3.5`) and a vision model (`qwen2.5vl`)
- NVIDIA GPU with a recent driver (for GPU embeddings; CPU works too)
- Node.js 18+ (frontend)

### Installation

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent
./scripts/setup_env.sh
conda activate angebot_agent
```

### Configuration

`setup_env.sh` creates `.env` from `.env.example`. The defaults target local services. Embeddings
default to `BAAI/bge-m3` (GPU); on CPU-only set `EMBEDDING_MODEL` to a small multilingual model
such as `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.

### Running

```bash
# 1. Data stores
docker compose -f infra/docker-compose.yml up -d

# 2. Build the frontend once (the API serves it)
cd frontend && npm install && npm run build && cd ..

# 3. Backend: MCP servers + API (Ollama must be running)
./scripts/run_dev.sh
```

Open the app at http://localhost:28734/ — the API serves the built UI and the API on the same
origin. For frontend development with hot reload, run `npm run dev` in `frontend/` instead (port
28735, which proxies the API).

## API

| Method | Path                  | Description                              |
| ------ | --------------------- | ---------------------------------------- |
| GET    | `/health`             | Liveness probe                           |
| GET    | `/readyz`             | Readiness probe (Postgres, Redis)        |
| POST   | `/api/search`         | Run the agent; streams progress over SSE |
| GET    | `/api/profile/{id}`   | Last location and recent searches        |
| GET    | `/docs`               | Interactive API documentation            |

## Project structure

```
backend/app/{api,agents,core,persistence,services,sources,schemas}
mcp_servers/{browse_search,geo}
frontend/
infra/
scripts/
docs/
```

## Roadmap

- [x] Project scaffold, typed configuration, health and readiness, local data stores
- [x] Official-prospekt browsing + local-VLM offer extraction (per-retailer recipes)
- [x] LangGraph agent: recipe decomposition, semantic matching, basket optimization
- [x] Long-term memory and scheduled cache refresh
- [ ] Vision-based flyer extraction (image/PDF) via a local VLM
- [ ] Observability, hardening, and containerized cloud deployment (AWS, Azure)

## Local development

This is configured for local and LAN use: the API binds `0.0.0.0` and CORS is open, and there is no
authentication. Lock those down (bind to localhost or put it behind an authenticating reverse proxy,
restrict CORS) before exposing it publicly. Postgres and Redis are bound to `127.0.0.1`.

## Contributing

Issues and pull requests are welcome. Run `ruff check` and `pytest` before submitting.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).

## Disclaimer

For personal and research use. Offer data is retrieved from third-party sources; respect each
provider's terms of service and `robots.txt`, and review them before any public deployment.
