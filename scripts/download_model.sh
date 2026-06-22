#!/usr/bin/env bash
# Download a HuggingFace model repo with aria2c (parallel, resumable). HF_TOKEN comes from .env.
#   scripts/download_model.sh <hf_repo_id> <dest_dir>
set -euo pipefail

REPO="${1:?usage: download_model.sh <hf_repo_id> <dest_dir>}"
DEST="${2:?usage: download_model.sh <hf_repo_id> <dest_dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA="${CONDA:-conda}"
ENV_NAME="${ENV_NAME:-angebot_agent}"

[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
AUTH=()
[ -n "${HF_TOKEN:-}" ] && AUTH=(--header="Authorization: Bearer ${HF_TOKEN}")

mkdir -p "$DEST"
"$CONDA" run -n "$ENV_NAME" python -c "
from huggingface_hub import HfApi
for s in HfApi().model_info('$REPO').siblings:
    print(s.rfilename)
" >"$DEST/.files.txt"
test -s "$DEST/.files.txt" || { echo "ERROR: empty file list for $REPO"; exit 1; }

while read -r f; do
  [ -z "$f" ] && continue
  mkdir -p "$DEST/$(dirname "$f")"
  aria2c -x16 -s16 -c -q "${AUTH[@]}" \
    -d "$DEST/$(dirname "$f")" -o "$(basename "$f")" \
    "https://huggingface.co/${REPO}/resolve/main/${f}"
done <"$DEST/.files.txt"

echo "done: ${REPO} -> ${DEST}"
