# Architecture - grocery-deals-agent

Agentic system that finds discounted German grocery offers for an itemized list or a recipe near
a given location, and serves the results in a React web app.

## Components

- Frontend: Vite + React + TypeScript + Tailwind + shadcn/ui. Streams agent progress over
  SSE/WebSocket. Toggles between single-store and cross-store baskets.
- Backend (FastAPI): async API that hosts the LangGraph agent and streams progress.
- Core agent (LangGraph): a supervisor with subagent nodes -
  Intent -> Recipe Decomposition -> Geo/Store Resolution -> Offer Retrieval -> Vision Extraction
  -> Matching -> Optimization -> Response. Durable Postgres checkpointing provides memory.
- MCP servers:
  - browse-search: tiered acquisition adapters (aggregator JSON, Playwright, browser automation).
    Source behaviour is defined in YAML config, not in branching code.
  - vision: vision-language model for flyer image/PDF extraction.
  - geo: geocoding and nearby-store lookup (OpenStreetMap plus a stores table).
- Persistence: Postgres + pgvector (users, searches, stores, cached offers, product embeddings)
  and Redis (cache, rate limiting, queue). Alembic migrations.
- Models: a vLLM reasoning model (OpenAI-compatible), an Ollama vision model, and embeddings.

## Principles

- Tiered acquisition: escalate to a costlier tier only when the cheaper one returns nothing.
- Config-driven sources: adding or disabling a retailer is a configuration change.
- Hybrid freshness: a scheduled crawl fills the cache; stale data triggers an on-demand refresh.

## Query flow

1. The UI posts `{location, query, basket_mode}` to `/api/search` (SSE).
2. The agent resolves the location to nearby stores and decomposes a recipe into items if needed.
3. Offer retrieval reads the cache; on a miss or stale entry it calls browse-search (tiered).
4. Matching maps requested items to offers using embeddings and unit normalization.
5. Optimization builds single-store and cross-store baskets with savings.
6. The response streams structured baskets, alternatives, and store data to the UI.
