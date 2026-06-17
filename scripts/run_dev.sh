#!/usr/bin/env bash
# Start the backend stack: MCP servers + FastAPI. Run the frontend separately:
#   cd frontend && npm install && npm run dev
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA="${CONDA:-/opt/miniforge/bin/conda}"
ENV_NAME="${ENV_NAME:-angebot_agent}"
cd "$ROOT"

start() {
  "$CONDA" run -n "$ENV_NAME" python "$1" &
  echo "  started $1 (pid $!)"
}

echo ">> MCP servers"
start mcp_servers/browse_search/server.py
start mcp_servers/geo/server.py
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
sleep 2

echo ">> API on http://0.0.0.0:28734 (docs at /docs)"
exec "$CONDA" run -n "$ENV_NAME" uvicorn app.main:app --host 0.0.0.0 --port 28734
