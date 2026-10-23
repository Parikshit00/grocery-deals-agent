# MCP server

The backend exposes its data as a [Model Context Protocol](https://modelcontextprotocol.io)
server, so any MCP client (Claude Code, Claude Desktop, Cursor, or your own agent) can use the
platform as a set of tools: which chains are covered, which stores are near a postcode, what is
on offer right now, and the cheapest way to buy a shopping list.

It is the same data and the same basket agent the web UI uses. The server lives in
`backend/app/mcp_server.py`, is built on the official Python SDK (`mcp==2.3.0`, `MCPServer`), and
is served by the FastAPI app as streamable HTTP at `/mcp` on the API port (28734).

## What it does and does not do

- **Read-only.** Every tool reads what earlier scans stored in Postgres. No tool starts a scan
  or writes to the database. To refresh offers, run a scan from the UI
  or `POST /api/scan`; the tools then see the new data.
- **Local models only.** `search_offers` embeds the query with the local bge-m3 model and
  `plan_basket` runs the basket agent on the local vLLM server. Apart from the geocoding lookup
  the UI makes too, and a product image the basket agent may download to look at an ambiguous
  offer, nothing leaves the machine.
- **Stateless.** Each tool call is one HTTP request with a JSON response. There are no sessions
  to resume and nothing to clean up when a client disconnects.
- Every tool is annotated `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`,
  so clients that honour annotations can run them without asking for confirmation.

Money is integer euro cents and pack sizes are integer base units (`g`, `ml`, `stk`), exactly as
in the [HTTP API](api.md).

## Tools

| Tool | Parameters | Returns |
|---|---|---|
| `list_chains` | none | Each chain's `id`, `label`, `loyalty` program (or `null`) and acquisition `tier` |
| `nearest_stores` | `location`, `radius_km=5` | Per chain: stores within the radius, nearest first, with `km`; last scan status and chosen store |
| `scan_status` | `location` | Per chain: last scan outcome and reason, chosen store with `km`, leaflets with validity dates and offer counts |
| `search_offers` | `location`, `query`, `radius_km=5`, `k_per_chain=5` | Best matching current offers per chain, with every price field and its `price_check` |
| `plan_basket` | `location`, `items`, `radius_km=5`, `max_stores=2`, `loyalty=false` | Single-store plans, the cheapest split plan, gaps, chains needing a scan, and a reason per item |

`location` is a German postcode or address. `radius_km` goes from 0.5 to 25. A chain that was
never scanned near the location shows up as `needs_scan`; that is the cue to run a scan, not an
error.

### list_chains

Static facts about the six chain agents. The tier says how a chain's offers are acquired: 1 a
structured feed, 2 leaflet pages read by the vision model, 3 a browser agent.

```json
{"chains": [
  {"id": "penny", "label": "Penny", "loyalty": "PENNY App", "tier": 1},
  {"id": "netto", "label": "Netto", "loyalty": "Netto plus App / PAYBACK", "tier": 2},
  {"id": "lidl", "label": "Lidl", "loyalty": "Lidl Plus", "tier": 3}
]}
```

(Abridged: the real answer lists all six chains.)

### nearest_stores

The stores each chain's last scan found near the postcode, with distances measured from the
exact address you pass. This is `GET /api/stores`.

```json
{"location": {"lat": 48.137, "lon": 11.575, "plz": "80331", "label": "80331 München"},
 "chains": {"penny": {"status": "ok", "reason": null,
                      "store": {"id": "...", "name": "...", "edition": "..."},
                      "stores": [{"id": "...", "name": "...", "address": "...", "km": 0.4}],
                      "finished_at": "2026-10-15T08:12:03Z"}}}
```

### scan_status

Ask this first. It tells a client which chains have usable data for the location and how fresh
it is. Each leaflet carries `valid_from`, `valid_to`, `dates_source` (`feed`, `text` or
`derived`), `status` (`complete` or `partial`) and the number of offers. A leaflet with
`expired_on` set is the last expired one, returned because no current leaflet exists.

### search_offers

Semantic search over the current offers of each chain's nearest scanned store, ranked per chain
with the same embedding search the basket agent uses (cosine similarity on bge-m3 vectors plus a
boost when an offer word ends with the query word, which suits German compounds). Only offers
valid on the shop day are searched (today, or Monday when today is Sunday). Non-food offers and
offers whose price failed the leaflet text check are left out.

Each hit is the full offer record plus the store and its distance:

```json
{"store": "PENNY Sendlinger Str.", "km": 0.4, "chain": "penny",
 "product_name": "Deutsche Markenbutter", "price_cents": 199, "loyalty_price_cents": 169,
 "pack_text": "250 g", "pack_qty": 250, "pack_unit": "g", "reference_price_cents": 796,
 "condition": "nur mit App", "min_qty": 1, "valid_from": "2026-10-12", "valid_to": "2026-10-17",
 "source": "feed", "price_check": "feed"}
```

The price fields are kept apart on purpose. `price_cents` is what everyone pays;
`loyalty_price_cents` only applies with the chain's app or card; for multi-buys,
`bundle_price_cents` is the exact price of `min_qty` packs. `price_check` says how far the price
can be trusted: `feed` came from a structured feed, `text` is a vision-model reading that was
found again in the page's text layer, `image_only` is a reading from a page without a text layer
to check against.

### plan_basket

Runs the basket agent over the cached offers, like `POST /api/basket`. For each item a local LLM
decides which offers ARE that product, in tool calls over the same search; code computes every
price, pack count and total. Expect a few seconds per item.

```json
{"location": "80331",
 "items": [{"name": "Butter", "qty": 500, "unit": "g"},
           {"name": "Spaghetti"},
           {"name": "Safran"}],
 "max_stores": 2, "loyalty": false}
```

The answer contains `single` (one plan per store, best coverage first), `split` (the cheapest
plan over at most `max_stores` stores, with `saves_cents` against the best single-store plan
that covers the same items), `gaps` (items with no current offer in the radius, here likely
`Safran`), `needs_scan`, and per item its `status` (`matched`, `no_offer` or
`matcher_unavailable`), the agent's `reason`, the accepted and rejected offers, and up to three
cheapest matches by unit price. The tool answers with an error while the LLM or the embedding
model is down.

## Connect a client

The examples use `gda.local` for the machine running the stack; use its host name or address.
The API port is bound to `BIND_ADDR`, which is `127.0.0.1` unless the local `.env` sets
`BIND_ADDR=0.0.0.0`, so for other machines on the network set that first. Drop the
`Authorization` header if `MCP_TOKEN` is not set.

=== "Claude Code"

    ```bash
    claude mcp add --transport http grocery-deals http://gda.local:28734/mcp \
      --header "Authorization: Bearer $MCP_TOKEN"
    claude mcp list   # shows grocery-deals as connected
    ```

    Add `--scope project` to write the entry to `.mcp.json` and share it with the repository.
    Then ask, for example: "Which chains near 80331 have current offers, and where is butter
    cheapest this week?"

=== "Claude Desktop"

    Claude Desktop starts local MCP servers as processes, so it reaches an HTTP server on the
    network through the `mcp-remote` bridge (needs Node.js). In
    `claude_desktop_config.json` (Settings, Developer, Edit Config):

    ```json
    {
      "mcpServers": {
        "grocery-deals": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "http://gda.local:28734/mcp", "--allow-http",
                   "--header", "Authorization: Bearer ${MCP_TOKEN}"],
          "env": {"MCP_TOKEN": "<token>"}
        }
      }
    }
    ```

    `--allow-http` is needed because the server speaks plain HTTP on the local network. Restart
    Claude Desktop afterwards.

=== "Cursor"

    In `~/.cursor/mcp.json`, or `.cursor/mcp.json` in a project:

    ```json
    {
      "mcpServers": {
        "grocery-deals": {
          "url": "http://gda.local:28734/mcp",
          "headers": {"Authorization": "Bearer <token>"}
        }
      }
    }
    ```

=== "Python"

    ```python
    import asyncio

    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    async def main() -> None:
        http = httpx2.AsyncClient(headers={"Authorization": "Bearer <token>"})
        async with Client(streamable_http_client("http://gda.local:28734/mcp",
                                                 http_client=http)) as client:
            result = await client.call_tool("search_offers",
                                            {"location": "80331", "query": "Butter"})
            print(result.structured_content)

    asyncio.run(main())
    ```

To poke at the tools by hand, `npx @modelcontextprotocol/inspector` opens a browser UI; choose
the Streamable HTTP transport and enter the URL and header above.

## Security

- **Read-only by construction.** The server registers five tools and nothing else: no
  resources, no prompts, no tool that writes, scans or fetches a URL the caller supplies. The worst a
  caller can do is read cached offer data and spend local GPU time with `plan_basket`.
- **Local network only.** The API port is bound to `127.0.0.1` by default. Opening it with
  `BIND_ADDR=0.0.0.0` is meant for a private network (LAN or VPN); do not publish the port to the
  internet. There is no TLS on this port, so put a reverse proxy with TLS in front if the network
  is not trusted.
- **Bearer token.** When `MCP_TOKEN` is set, every request to `/mcp` must carry
  `Authorization: Bearer <token>` and anything else gets `401`. The comparison is constant-time.
  Use a long random value, for example `openssl rand -hex 32`, and keep it in the local `.env`.
  Without a token the endpoint is open to anyone who can reach the port, which is acceptable only
  on a machine bound to `127.0.0.1`.
- **DNS rebinding.** The SDK's Host header guard only admits `localhost` names, which would lock
  out clients on the network, so it is turned off. A malicious web page in a browser on the same
  network could then reach `/mcp`; the token closes that gap, so set one whenever the port is
  reachable from other machines.
- **Untrusted text.** Offer names and descriptions come from retailer pages. A client should
  treat tool results as data, not as instructions, like any other content fetched from the web.

See the [threat model](security.md) for how this fits the platform's OWASP LLM and agentic
risk mapping.
