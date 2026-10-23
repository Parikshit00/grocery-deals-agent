# Demo recording guide

Everything runs on the GPU server; you open it in a browser on another machine in the same
private network (LAN or VPN). Replace `<server>` below with the server's address.

## Launch

```bash
cd grocery-deals-agent
make init           # once: creates .env with generated secrets
# in .env: BIND_ADDR=0.0.0.0 and LANGFUSE_URL=http://<server>:28736 (Langfuse login redirects there)
make demo-up        # models + observability + app, then one scan of 80331 to warm every chain; prints the URLs
make demo-down      # afterwards: stop the model servers and free both GPUs
```

`make demo-prep DEMO_PLZ=50667` warms another location; cached chains answer at once.

| Open in your browser | URL | Login |
|---|---|---|
| App | `http://<server>:28735` | none |
| Langfuse (traces, evals) | `http://<server>:28736` | `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` from `.env` |
| Grafana (live GPU and model metrics) | `http://<server>:28737/d/gda-vllm` | none (viewer) |

Before recording: check that the GPUs are quiet (`nvidia-smi`); other workloads on the same GPUs
slow the agents down. Record at 1920x1080 with browser zoom at 100%.

## Shot list

### V1 Agents at work (about 2 to 3 min)
1. App, **Offers** view. Location `80331`, radius 5 km.
2. Click **Force rescan**, all chains selected. Six agent windows start at once.
3. What to point out while it runs:
   - Penny, Kaufland, Aldi Sued: tier 1, official feeds: store found, km, offers count up in seconds.
   - Netto: tier 2: leaflet pages flow in as thumbnails while the local vision model reads each
     page; verified prices vs flagged ones.
   - Lidl: tier 3: a live browser window picks the nearest store on lidl.de; with
     `FORCE_HEAL=lidl:select_store` in `.env`, one step is deliberately skipped and the
     self-healing navigator visibly fixes it (highlighted "self-healed").
   - REWE: the browser opens the market page and stops honestly: "blocked by bot protection".
4. End on the totals strip (stores, pages read, offers, flagged, time).

### V2 Recipe to cheapest plan (about 2 min)
1. **Plan** view, recipe mode: `Lasagne fuer 4`, servings 4.
2. The model's thinking streams, then collapses into editable ingredient chips. Remove one chip
   (for example salt) to show control.
3. Run the basket: each ingredient's agent searches and accepts or rejects offers.
4. Compare the single-store and split plans (savings, km, unit prices); open
   **Why this match** on one item to show the tool calls and a rejected near-miss.

### V3 Observability (about 2 min)
1. **Runs** view: the scan's runs per chain with timings, pages, offers, heals.
2. **Open trace** on the Netto run: in Langfuse, the trace tree (scan, chain, leaflet, page reads)
   with each vision-model generation's tokens and latency; then the basket trace with tool calls.
3. Start another force rescan and switch to Grafana: requests, tokens/s, KV cache and GPU panels
   move while the agents work.

### V4 Evals (about 1 min)
1. App **Evals** tab: scores per suite (extraction vs gold labels, product matching, baskets).
2. Langfuse **Datasets**: the same suites as experiments, side by side per model.

### V5 Architecture (stills)
- Animated flow: `docs/architecture/agent-flow.svg` (plays inline on GitHub), with
  `docs/media/agent-flow.mp4` and `agent-flow.gif` ready for the video edit.
- Detailed architecture: `docs/architecture/architecture.png` / `architecture.svg` (editable source:
  `architecture.drawio`, opens in the draw.io plugin).

## Re-takes
- Force rescan replays any chain; cached chains answer instantly, so V2 stays fast.
- `make demo-prep` re-warms the caches.
