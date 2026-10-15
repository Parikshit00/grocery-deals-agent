# ADR 0005 - A read-only MCP server for external agents

Status: Accepted | Date: 2026-10-15 | Supersedes the MCP part of ADR 0002 decision 4

## Context
ADR 0002 kept MCP out until a real external consumer existed. The earlier MCP server had no
outside consumer: the backend called itself through it, and one of its tools wrote to the
database without authentication. Both were removed in the rebuild.

The consumer now exists. The cache holds current, verified offers for every scanned location,
and the natural clients for that data are other agents: coding assistants, desktop assistants
and custom agents that already speak the Model Context Protocol. Giving them a REST API would
mean writing a client per assistant; MCP is the interface they share.

## Decision
1. The backend serves MCP itself at `/mcp` (streamable HTTP, stateless, JSON responses) with
   the official `mcp` SDK 2.x. There is no separate service and no second implementation: the
   tools call the same functions as the HTTP API, so validation and errors match.
2. Five tools, all read-only and annotated as such: `list_chains`, `nearest_stores`,
   `scan_status`, `search_offers` and `plan_basket`. No tool starts a scan, writes to the
   database or contacts a retailer, so scans stay user-triggered. `plan_basket` runs the same
   local basket agent as `POST /api/basket`; every model call stays on the local vLLM servers.
3. Arguments are bounded (location length, radius, results per chain, list size). When
   `MCP_TOKEN` is set, every request needs the bearer token. The SDK's DNS rebinding guard is
   off because clients connect over the LAN; the bind address is the API's.

## Consequences
- External agents can ask what is on offer near a postcode, or plan a shopping list against the
  cache, without any new load on retailer sites.
- One more entry point to secure. It is threat T-12 in the [threat model](../security.md):
  read-only tools, optional token, and results that clients must treat as untrusted text, since
  offer names come from retailer leaflets.
- Tests call every tool in-process through the SDK client and over HTTP, including the token
  check, so an SDK upgrade that breaks the surface fails CI.
