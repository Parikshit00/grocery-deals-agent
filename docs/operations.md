# Operations

## Make targets

| Target | What it does |
|---|---|
| `make init` | Creates or completes `.env` from `.env.example`, generates every secret and the Langfuse project keys, creates `MODELS_DIR`. Safe to re-run: existing values are kept. |
| `make models-pull [NAME=...]` | Downloads the pinned weights listed in `infra/models.txt` into `MODELS_DIR`. |
| `make models-lock` / `make models-verify [NAME=...]` | Records or checks the sha256 of every weight file (`infra/models.sha256`). |
| `make models-up` / `make models-down` | Starts both vLLM servers and waits until healthy / stops them and frees the GPUs. |
| `make obs-up` / `make obs-down` | Starts or stops Langfuse, Prometheus, the DCGM exporter and Grafana. Data volumes are kept. |
| `make up` / `make down` / `make logs` | The app itself: Postgres, backend (runs migrations on start), frontend. |
| `make demo-up` / `make demo-prep` / `make demo-down` | The one-command demo: everything above plus cache warm-up; `demo-prep` re-warms caches; `demo-down` frees the GPUs. |
| `make lint` / `make test` / `make check` | ruff, mypy and pytest in the backend dev image, exactly as CI runs them. |
| `make canary CHAIN=... PLZ=... [FORCE=1]` | Live check of one chain against its real website. |
| `make lock` | Re-pins runtime and dev Python dependencies with `uv pip compile`. |

The Compose services are grouped into profiles: the default one (Postgres, backend, frontend),
`models` (the two vLLM servers) and `observability`. Each profile starts and stops on its own, so
the GPUs can be released without losing traces or dashboards.

## Troubleshooting

??? question "A vLLM server does not become healthy, or fails with CUDA out of memory"
    The development box is shared, and another tenant's job may already hold GPU memory. Check
    with `nvidia-smi`. Each server reserves a share of its GPU set by
    `LLM_GPU_MEMORY_UTILIZATION` and `VLM_GPU_MEMORY_UTILIZATION` in `.env` (default `0.80`).
    Lower the share until it fits next to the other job, then `make models-up` again. If the
    model loads but the KV cache is too small for 32k context, vLLM refuses to start; lowering
    the share further will not help, freeing the GPU will.

??? question "The agents are much slower than usual"
    Decode speed drops sharply when another job saturates the same GPU's compute. The Grafana
    panels **GPU utilization** and **Waiting requests** show it: high utilization with few running
    requests of ours means contention. Record demos when the GPUs are otherwise idle.

??? question "The UI, Langfuse or Grafana cannot be reached from another machine"
    Ports bind to `BIND_ADDR` from `.env` (default `127.0.0.1`). Set `BIND_ADDR=0.0.0.0`, run
    `make up` and `make obs-up` again so the containers are recreated with the new bindings, and
    connect through the VPN to `http://<host>:28735`, `:28736` and `:28737`. Prometheus stays on
    localhost by design; use an SSH tunnel for it.

??? question "How do I log in to Langfuse?"
    Open `http://<host>:28736` and sign in with `LANGFUSE_ADMIN_EMAIL` and
    `LANGFUSE_ADMIN_PASSWORD` from `.env`. The organisation, project, admin user and API keys are
    created headlessly on the first start, so there is no sign-up step. Keep these `.env` values
    stable after that first start; if they must change, create new API keys in the Langfuse UI
    and put them into `.env`.

??? question "Traces do not show up in Langfuse"
    Check `TRACING_ENABLED=true` and the `LANGFUSE_*` keys in `.env`, then that the observability
    profile is up (`make obs-up`). The backend only reads these settings at start, so restart it
    with `make up` after a change. Spans are exported in batches and may need a few seconds.

??? question "Restarting a single profile"
    ```bash
    make models-down && make models-up   # model servers only
    make obs-down && make obs-up         # observability only, data kept
    make up                              # rebuild and restart the app containers
    ```
    The backend tolerates model servers that come up later: `GET /api/status` reports each
    model server, Postgres and the embedding model as up or down, and the UI shows it.

??? question "A chain fails or is reported as blocked"
    `make canary CHAIN=<chain> PLZ=80331 FORCE=1` reruns one chain live, prints its events and
    checks every URL it requested against that host's `robots.txt`.
    REWE is expected to end as blocked while its site sits behind a bot challenge. For other
    chains, the run's trace in Langfuse shows the failing step, including any heal attempts.

??? question "Disk is filling up"
    Model weights dominate. Remove unused models from `MODELS_DIR`; Prometheus keeps 7 days and
    ClickHouse has a TTL, so observability data stays bounded. `docker system df` shows image
    and build cache usage.
