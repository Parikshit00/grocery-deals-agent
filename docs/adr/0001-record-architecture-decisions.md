# ADR 0001 - Record architecture decisions; foundational choices

Status: Accepted | Date: 2026-06-17

## Context
Building a production-grade agentic grocery-deal search with hard constraints: local models only
(no Anthropic/cloud in the agent path), no hardcoded business logic, token efficiency, conda env
`angebot_agent`, 2x A40 GPUs, containerized + cloud-portable.

## Decisions
1. **Agent framework: LangGraph** - explicit state, supervisor->subagent pattern, durable Postgres
   checkpointing (= memory), works with any OpenAI-compatible/local backend.
2. **Reasoning via vLLM (OpenAI-compatible, GPU 0); vision via Ollama qwen2.5vl (GPU 1).**
   Reached through one swappable client (`app/core/llm.py`); no Anthropic branch by design.
3. **Per-retailer official-prospekt acquisition**: deterministic Playwright browse -> local VLM page
   extraction -> validity cache. No third-party aggregator API; browser-use is a bounded fallback
   for sites that change.
4. **Validity-based freshness**: cache per (retailer, region) with the prospekt's valid_from/valid_to;
   scan once per prospekt week, retrieve thereafter.
5. **Persistence**: Postgres + pgvector + Redis; Alembic migrations.
6. **Tools exposed as MCP servers** (`browse-search`, `vision`, `geo`) consumed by the agent via
   `langchain-mcp-adapters`.

## Consequences
- Adding/disabling a retailer is a config change, not code.
- The agent path is provider-agnostic and cloud-free, satisfying the local-only constraint.
- Higher tiers (browser/VLM) are isolated and optional, keeping token/compute cost low by default.
