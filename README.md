# grocery-deals-agent

> Agentic AI assistant that finds grocery discounts, optimizes shopping lists, and searches supermarket offers in Germany using LLM agents.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)
![Lint](https://img.shields.io/badge/lint-ruff-261230)
![Status](https://img.shields.io/badge/status-early%20development-orange)

## Overview

Given a location (postcode or address) and either a shopping list or a recipe, the agent finds
nearby supermarkets (Penny, Rewe, Kaufland, Lidl, Aldi, Netto, Edeka, and others), retrieves their
current weekly offers, matches them to the requested items, and builds the cheapest basket, either
at a single store or split across stores.

All reasoning, vision, and embedding models run locally; the agent path uses no external LLM API.

## Features

- Location-aware search from a postcode or street address.
- Shopping list or recipe input, with recipes decomposed into ingredients.
- Single-store and cross-store basket optimization with savings.
- Tiered data acquisition: aggregator APIs first, with scraping, browser automation, and
  vision-based flyer reading as fallbacks.
- Local-first models served via vLLM and Ollama.
- Production foundations: typed configuration, health and readiness probes, structured logging,
  and database migrations.

## Architecture

A FastAPI service hosts a LangGraph agent that calls tools exposed by MCP servers (browse-search,
vision, geo). Offers are cached in PostgreSQL (pgvector) with Redis for caching and rate limiting.
See [docs/architecture.md](docs/architecture.md), [docs/data-sources.md](docs/data-sources.md),
and [docs/adr/](docs/adr/).

## Tech stack

| Layer            | Technology                                      |
| ---------------- | ----------------------------------------------- |
| Agent            | LangGraph                                        |
| Tooling          | MCP servers (browse-search, vision, geo)         |
| Backend          | FastAPI, Pydantic, SQLAlchemy, Alembic           |
| Data             | PostgreSQL + pgvector, Redis                      |
| Models           | vLLM (reasoning), Ollama (vision, embeddings)     |
| Frontend         | React, Vite, TypeScript, Tailwind                |
| Quality          | Ruff, mypy, pytest, Docker                        |

## Getting started

### Prerequisites

- Python 3.11+ and Conda
- Docker and Docker Compose
- NVIDIA GPU with recent drivers (for local model serving)
- Node.js 18+ (frontend; optional during early development)

### Installation

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent
./scripts/setup_env.sh
conda activate angebot_agent
```

### Configuration

`setup_env.sh` creates `.env` from `.env.example`. Review and adjust the values as needed.

### Running

```bash
# Start data stores
docker compose -f infra/docker-compose.yml up -d

# Pull local models, then serve the reasoning model in a separate terminal
./scripts/pull_models.sh
./scripts/serve_vllm.sh

# Start the API
uvicorn app.main:app --host 0.0.0.0 --port 28734
```

## API

| Method | Path      | Description                       |
| ------ | --------- | -------------------------------- |
| GET    | `/health` | Liveness probe                   |
| GET    | `/readyz` | Readiness probe (Postgres, Redis)|
| GET    | `/docs`   | Interactive API documentation    |

Postgres and Redis are bound to `127.0.0.1`. The API listens on the host and port from `.env`.

## Project structure

```
backend/app/{api,agents,mcp_clients,core,memory,persistence,services,schemas}
mcp_servers/{browse_search,vision,geo}
frontend/
infra/{docker,helm,terraform}
scripts/
docs/
```

## Roadmap

- [x] Project scaffold, typed configuration, health and readiness endpoints, local data stores
- [ ] Location resolution and offer retrieval via MCP (marktguru)
- [ ] LangGraph agent: recipe decomposition, item matching, basket optimization
- [ ] Vision-based flyer extraction and scraping/browser fallbacks
- [ ] React web interface
- [ ] Observability, containerized deployment, and cloud targets (AWS, Azure)

## Contributing

Issues and pull requests are welcome. Run `ruff check` and `pytest` before submitting.

## License

No license has been set yet. Until a `LICENSE` file is added, all rights are reserved.

## Disclaimer

For personal and research use. Offer data is retrieved from third-party sources; respect each
provider's terms of service and `robots.txt`, and review them before any public deployment.
