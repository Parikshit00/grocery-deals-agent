#!/usr/bin/env bash
# Create the conda environment and install backend dependencies.
set -euo pipefail

CONDA="${CONDA:-conda}"
ENV_NAME="angebot_agent"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! "$CONDA" env list | grep -qE "^\s*${ENV_NAME}\s"; then
  echo ">> Creating conda env ${ENV_NAME} (python 3.11)"
  "$CONDA" create -y -n "$ENV_NAME" python=3.11
else
  echo ">> conda env ${ENV_NAME} already exists"
fi

echo ">> Installing backend (core + agents + dev) from ${ROOT}"
"$CONDA" run -n "$ENV_NAME" python -m pip install --upgrade pip
"$CONDA" run -n "$ENV_NAME" python -m pip install -e "${ROOT}[agents,dev]"

if [ ! -f "${ROOT}/.env" ]; then
  cp "${ROOT}/.env.example" "${ROOT}/.env"
  echo ">> Created .env from .env.example"
fi

echo ">> Done. Activate with: conda activate ${ENV_NAME}"
echo ">> Scraping extras (Playwright) are optional:"
echo "   ${CONDA} run -n ${ENV_NAME} python -m pip install -e \"${ROOT}[scraping]\" && \\"
echo "   ${CONDA} run -n ${ENV_NAME} python -m playwright install chromium"
