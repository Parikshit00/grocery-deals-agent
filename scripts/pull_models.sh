#!/usr/bin/env bash
# Ensure required Ollama models are present (vision + reasoning).
# Embeddings download automatically via sentence-transformers on first use.
set -euo pipefail

if ! pgrep -x ollama >/dev/null 2>&1; then
  echo ">> Ollama not running. Start it with: ollama serve &"
  echo "   (continuing; 'ollama pull' will start it on demand)"
fi

VISION_MODEL="${VISION_MODEL:-qwen2.5vl:7b}"
REASONING_MODEL="${LLM_MODEL:-qwen3.5:latest}"

for m in "$VISION_MODEL" "$REASONING_MODEL"; do
  echo ">> ollama pull ${m}"
  ollama pull "$m"
done

echo ">> Models ready:"
ollama list
