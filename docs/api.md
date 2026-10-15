---
hide:
  - toc
---

# API

The backend is a FastAPI app on port 28734. Long-running work streams as Server-Sent Events, so
the UI can show each agent's progress while it happens. A running instance also serves Swagger UI
at `/docs` and the raw schema at `/openapi.json`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/scan` | Start a scan for a location and a set of chains; SSE stream of its agent events (stores, pages, offers, heal steps, outcomes) |
| `GET` | `/api/scan/{scan_id}/events` | Reattach to a running scan's event stream, for example after a reload |
| `POST` | `/api/scan/{scan_id}/cancel` | Cancel a running scan |
| `GET` | `/api/stores` | Nearby stores per chain, for the store picker |
| `GET` | `/api/offers` | Current cached offers per chain and leaflet for a location |
| `GET` | `/api/runs` | Recent chain runs with timings, counts and their Langfuse trace id |
| `POST` | `/api/recipe` | Decompose a recipe into ingredients (SSE, streams the model's thinking) |
| `POST` | `/api/basket` | Match items to offers and plan the cheapest shop; the response carries each item's match trace |
| `GET` | `/api/evals` | Latest eval results per model |
| `GET` | `/api/status` | Model servers, database and embedding model up or down |
| `POST` | `/mcp` | Model Context Protocol endpoint (streamable HTTP) with five read-only tools, see [MCP server](mcp.md) |
| `GET` | `/health`, `/readyz` | Liveness and readiness probes |

The reference below is rendered from the OpenAPI schema that the docs build exports from the app
itself, so it cannot drift from the code. <a href="openapi.json">Download openapi.json</a>.

??? info "Regenerating the schema locally"
    The docs workflow runs this in the backend dev image before `mkdocs build`:

    ```bash
    make dev-image
    docker run --rm -e PYTHONPATH=/repo/backend -v "$PWD":/repo -w /repo grocery-deals-agent-dev \
      sh -c 'mkdir -p docs/api && python -c "import json; from app.main import app; \
      print(json.dumps(app.openapi(), indent=1))" > docs/api/openapi.json && mkdocs build --strict'
    ```

<redoc spec-url="openapi.json" hide-hostname="true" expand-responses="200"></redoc>
<script src="https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js"></script>
