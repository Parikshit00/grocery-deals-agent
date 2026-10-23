# ADR 0001 - Record architecture decisions; foundational choices

Status: Accepted | Date: 2026-06-17 | Decision 1 superseded by ADR 0002, decisions 3-4 by ADR 0003,
the model in decision 2 (Qwen3-VL) by ADR 0004

## Context
Building a production-grade agentic grocery-deal search with hard constraints: local models only
(no Anthropic/cloud in the agent path), no hardcoded business logic, token efficiency, conda env
`angebot_agent`, 2x A40 GPUs, containerized + cloud-portable.

## Decisions
1. **Agent framework: LangGraph** - explicit state, supervisor->subagent pattern, works with any
   OpenAI-compatible/local backend.
2. **Reasoning via vLLM (OpenAI-compatible, GPU 0); vision via vLLM Qwen3-VL (GPU 1).**
   Reached through one swappable client (`app/clients/llm.py`); no Anthropic branch by design.
3. **Per-retailer official-prospekt acquisition**: deterministic Playwright browse -> local VLM page
   extraction -> validity cache. No third-party aggregator API.
4. **Validity-based freshness**: cache per (retailer, region) with the prospekt's valid_from/valid_to;
   scan once per prospekt week, retrieve thereafter.
5. **Persistence**: Postgres.

## Consequences
- Adding/disabling a retailer is a config change, not code.
- The agent path is provider-agnostic and cloud-free, satisfying the local-only constraint.
- Higher tiers (browser/VLM) are isolated and optional, keeping token/compute cost low by default.
