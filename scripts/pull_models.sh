#!/usr/bin/env bash
# Verify the local vLLM model checkpoints are present (download via scripts/download_model.sh).
# The bge-m3 embedding model downloads automatically via sentence-transformers on first use.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/home/parikshitk/models}"
LLM_MODEL_DIR="${LLM_MODEL_PATH:-$MODELS_DIR/Qwen3-30B-A3B-Thinking-2507-FP8}"
VLM_MODEL_DIR="${VLM_MODEL_PATH:-$MODELS_DIR/Qwen3-VL-32B-Instruct-FP8}"

missing=0
for d in "$LLM_MODEL_DIR" "$VLM_MODEL_DIR"; do
  if [ -f "$d/config.json" ]; then
    echo ">> OK  $d"
  else
    echo ">> MISSING  $d"
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  echo ">> Download missing checkpoints with: scripts/download_model.sh <hf-repo-id> <dest-dir>"
  exit 1
fi
echo ">> All vLLM model checkpoints present."
