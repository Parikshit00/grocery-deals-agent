# ADR 0002 - Scripted navigation with bounded self-healing; no agent framework

Status: Accepted | Date: 2026-09-28 | Supersedes ADR 0001 decision 1

## Context
An experiment (2026-07-09) made an LLM browser agent (browser-use, text-only reasoning model) drive
every scan. Measured against the scripted recipes on the same week it regressed on every chain:
no capture completed, Kaufland 41 offers vs 491, Netto 22 vs about 235, six watchdog aborts and
repeated VLM timeouts. It also read leaflets from low-resolution viewport screenshots, lost the
real validity dates, could leave the retailer's domain (web search and JS evaluation tools), and
pulled cloud-LLM SDKs plus telemetry into the runtime image. The LangGraph "search graph" was a
straight four-step pipeline, and a later "supervisor" computed its decision in code and discarded
the model's answer.

## Decision
1. Each chain runs scripted Playwright steps, and every step has an explicit post-condition
   (store selected, offers view open, pages captured).
2. Only when a post-condition fails does a small custom navigator take over that one step. It
   uses the local vision model with a screenshot plus numbered page elements, a five-action space
   (click, type from a fixed input set, scroll, back, done), the chain's domain allowlist, and
   strict step and time budgets. It has no web search, no model-supplied JavaScript and no
   telemetry. Every heal is logged to `scan_runs.self_heal`; code is never rewritten automatically.
3. The second place a model decides is the basket: a tool-calling agent rewords queries and
   accepts or rejects candidate offers. Code computes every price and total.
4. No agent framework (LangGraph, browser-use) and no MCP server: orchestration is plain asyncio,
   and MCP stays out until a real external consumer exists.

## Consequences
- The normal path is fast and deterministic; model calls are spent where they add robustness.
- "Agentic" is measured, not claimed: heal recovery rate, navigator accuracy on recorded tasks,
  match precision and tool-call validity are reported in the README.
- Selector drift degrades into a logged heal instead of a silent empty scan.
