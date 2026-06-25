#!/usr/bin/env bash
# Start the FastAPI backend (serves the built frontend at /). For frontend dev with HMR:
#   cd frontend && npm install && npm run dev
# Models are served separately: scripts/serve_llm.sh (GPU0) + scripts/serve_vlm.sh (GPU1).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA="${CONDA:-/opt/miniforge/bin/conda}"
ENV_NAME="${ENV_NAME:-angebot_agent}"
cd "$ROOT/backend"

echo ">> API on http://0.0.0.0:28734 (docs at /docs)"
exec "$CONDA" run -n "$ENV_NAME" --no-capture-output \
  uvicorn app.main:app --host 0.0.0.0 --port 28734
