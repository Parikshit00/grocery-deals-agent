# Getting started

## Prerequisites

| Need | Why |
|---|---|
| Linux host with two NVIDIA GPUs (developed on 2x A40, 48 GB each) | GPU 0 serves the language model, GPU 1 the vision model |
| NVIDIA driver and the NVIDIA Container Toolkit | the vLLM and DCGM containers need GPU access |
| Docker Engine with Compose v2 | everything runs as one Compose project |
| `python3` and [`uv`](https://docs.astral.sh/uv/) on the host | `make init` generates secrets with Python; `make models-pull` runs the Hugging Face CLI through `uvx` |
| Disk for the weights | Qwen3.5-9B is about 19 GB, bge-m3 about 2 GB; the optional Qwen3.6-35B-A3B-FP8 adds about 37 GB |

The Makefile uses `sudo docker` automatically when the plain client cannot reach the daemon.

## Bring it up

```bash
git clone https://github.com/Parikshit00/grocery-deals-agent.git
cd grocery-deals-agent

make init          # .env from .env.example, random secrets and API keys, MODELS_DIR
make models-pull   # pinned weights from infra/models.txt (NAME=qwen3.5-9b for one model)
make models-verify # optional: check them against infra/models.sha256
make demo-up       # models, observability, app and migrations; warms caches and prints the URLs
```

`make demo-up` is the one-command path used for the demo videos. The same stack in steps:

```bash
make models-up     # both vLLM servers, waits until healthy
make obs-up        # Langfuse, Prometheus, DCGM exporter, Grafana
make up            # Postgres, backend (runs Alembic migrations), frontend
```

`make demo-down` stops the model servers and frees both GPUs; `make obs-down` stops the
observability stack and keeps its data volumes.

## Where things are

| Service | URL | Login |
|---|---|---|
| App (React UI) | `http://<host>:28735` | none |
| API (FastAPI, `/docs` for Swagger) | `http://<host>:28734` | none |
| Langfuse (traces, sessions, datasets, experiments) | `http://<host>:28736` | `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` from `.env` |
| Grafana (vLLM and GPU dashboard) | `http://<host>:28737` | anonymous viewer |
| Prometheus | `http://127.0.0.1:28738` | local only |

Ports bind to `BIND_ADDR` from `.env`, which defaults to `127.0.0.1`. Set `BIND_ADDR=0.0.0.0` in
your local `.env` to open the UI, Langfuse and Grafana from another machine, for example over a
VPN; Prometheus always stays on localhost. The repository never contains host addresses.

## A first run

1. Open the app, enter postcode `80331` and start a scan. Six agent windows show each chain's
   tier, chosen store and distance, live browser frames or page thumbnails, and the offers found.
2. Switch to **Plan**, type `Lasagne fuer 4` in recipe mode and run the basket. The recipe
   breaks down into editable ingredient chips, each ingredient's agent picks offers with tool
   calls, and the planner compares a single-store plan with a split one.
3. In **Runs**, use **Open trace** to jump to the run's trace in Langfuse.

## Without a GPU

The [replay demo](https://parikshit00.github.io/grocery-deals-agent/replay/) is the real UI
playing back one recorded session: a scan of all six chains, a recipe and its basket, with the
recorded timing. It needs no backend and no model server, and a banner marks it as a replay.
Retailer images are stripped when recording, so it shows placeholders where the live app shows
leaflet pages and browser frames.

To record your own, run a session against a live stack and build the replay UI from it:

```bash
uv run --no-project --with httpx python backend/tests/record_replay.py --location 80331 \
    --recipe "Lasagne" --servings 4   # writes frontend/public/replay/session.json
cd frontend
npm ci
npm run build:replay                  # static site in frontend/dist/replay
npx vite preview --outDir dist/replay
```

Only `npm run build:replay` ships the recorded session; the normal build and the frontend
container never contain it. The docs workflow publishes the committed
`frontend/public/replay/session.json` next to these pages and fails if it is missing.

## Development loop

All checks run in the backend dev image, the same one CI uses:

```bash
make lint   # ruff and mypy
make test   # pytest
make check  # both
```

For a live check of a single chain against its real website:

```bash
make canary CHAIN=penny PLZ=80331 FORCE=1
```
